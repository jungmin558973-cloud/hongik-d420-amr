import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';

const $ = id => document.getElementById(id);
const owner = crypto.randomUUID();
const labels = {brake:'1m 후 급정지',turn:'1m 후 90° 턴',bump:'1.2m + 언덕 10cm 진입',teleop:'텔레옵 수동 조종',follow:'사람 따라가기'};
let token = '', mode = 'preview', state = null, selected = 'brake', pending = false;
let lastPacket = 0, allowReal = false, serverOnline = false;
let errorUntil = 0, lastPhase = '', lastMode = '', lastElapsed = 0;
let bumpMeasured = false;
let trayPending = false;
let lastCourseVersion = 0;
let drive='scenario',teleopControl='',teleopSequence=0,teleopSending=false,teleopDirty=false;
const heldKeys=new Set(),heldPointers=new Map();
const keyMotion={KeyW:'forward',ArrowUp:'forward',KeyS:'backward',ArrowDown:'backward',KeyA:'left',ArrowLeft:'left',KeyD:'right',ArrowRight:'right'};
let lastTrayProfile='';

function setDrive(next){
  if(state?.active){notice('현재 조종/시험을 먼저 중지해 주세요.',true);return;}
  drive=next;document.body.dataset.driveLayout=drive;
  $('scenario-controls').hidden=drive!=='scenario';$('manual-controls').hidden=drive!=='teleop';
  $('follow-controls').hidden=drive!=='follow';
  document.querySelectorAll('[data-drive]').forEach(b=>{b.classList.toggle('selected',b.dataset.drive===drive);b.setAttribute('aria-pressed',String(b.dataset.drive===drive));});
  selected=drive==='scenario'?'brake':drive;clearManual();updateCourse();updateButtons();
}

function setCameraView(expanded){
  document.body.classList.toggle('camera-focus',expanded);
  $('camera-enlarge').textContent=expanded?'기본 화면으로':'카메라 크게 보기';
  $('camera-enlarge').setAttribute('aria-pressed',String(expanded));
  requestAnimationFrame(fitCameraView);
}
function fitCameraView(){
  const screen=document.querySelector('.camera-screen');
  if(document.body.classList.contains('camera-focus')&&innerWidth>700){
    const top=screen.getBoundingClientRect().top+scrollY;
    const height=`${Math.max(240,innerHeight-top-16)}px`;
    if(screen.style.height!==height)screen.style.height=height;
  }else screen.style.height='';
}
window.addEventListener('resize',()=>requestAnimationFrame(fitCameraView));
$('camera-enlarge').onclick=()=>setCameraView(!document.body.classList.contains('camera-focus'));

async function prepareFollow(){
  pending=true;updateButtons();notice('YOLO와 정렬 깊이 영상을 준비합니다. 아직 주행하지 않습니다.');
  try{const r=await request('follow-prepare');renderState(r.state);}
  catch(e){notice(e.message,true);}finally{pending=false;updateButtons();}
}
async function startFollow(){
  if(pending||state?.active)return;
  pending=true;updateButtons();
  try{
    const r=await request('follow-start',{mode,owner,target_id:state?.follow?.target_id,distance:Number($('follow-distance').value),config:{speed:.12}});
    state=r.state;selected='follow';clearTrail();updateCourse();renderState(state);
  }catch(e){notice(e.message,true);}finally{pending=false;updateButtons();}
}

function manualOwned(){return state?.active&&state.kind==='teleop'&&teleopControl&&state.control_id===teleopControl;}
function manualAxes(){
  const motions=new Set([...heldKeys].map(key=>keyMotion[key]).concat([...heldPointers.values()]));
  document.querySelectorAll('[data-motion]').forEach(b=>b.classList.toggle('held',motions.has(b.dataset.motion)));
  return {forward:Number(motions.has('forward'))-Number(motions.has('backward')),turn:Number(motions.has('left'))-Number(motions.has('right'))};
}
function clearManual(){heldKeys.clear();heldPointers.clear();manualAxes();}
async function sendManual(){
  if(!manualOwned()||document.hidden)return;
  if(teleopSending){teleopDirty=true;return;}
  teleopSending=true;teleopDirty=false;
  try{await request('teleop-input',{...manualAxes(),speed:Number($('teleop-speed').value)/100,yaw_speed:Number($('teleop-yaw').value),owner,control_id:teleopControl,sequence:++teleopSequence});}
  catch(e){clearManual();notice(e.message,true);await stop();}
  finally{teleopSending=false;if(teleopDirty){teleopDirty=false;queueMicrotask(sendManual);}}
}
async function armManual(){
  if(state?.active){if(manualOwned())await stop();return;}
  pending=true;updateButtons();clearManual();
  try{
    const {bump_width,bump_height,...parameters}=config();parameters.speed=Number($('teleop-speed').value)/100;
    const result=await request('teleop-start',{mode,config:parameters,owner});
    teleopControl=result.state.control_id;teleopSequence=0;state=result.state;selected='teleop';clearTrail();updateCourse();renderState(state);await sendManual();
  }catch(e){notice(e.message,true);}finally{pending=false;updateButtons();}
}

function predictionParameters(){return {lead:Number($('prediction-lead').value),angle_limit:Number($('prediction-limit').value),roll_sign:Number($('prediction-roll-sign').value),pitch_sign:Number($('prediction-pitch-sign').value),swap_axes:$('prediction-swap').checked};}
async function selectTray(control){
  trayPending=true;updateButtons();
  try{const r=await request('tray-mode',{mode,control,parameters:predictionParameters()});renderState(r.state);notice(mode==='preview'?'트레이 미리보기 모드를 바꿨습니다.':control==='PREDICTIVE'?'대기 중에는 수평 유지, 주행 중에는 명령/코스 예측 보정을 적용합니다.':'ESP32에 모드 요청을 보냈습니다. 실측 상태를 확인해 주세요.');}
  catch(e){notice(e.message,true);}finally{trayPending=false;updateButtons();}
}

function renderTrayControl(next){
  const profile=next.tray_controls?.[mode]||{mode:'OFF',parameters:{lead:.2,angle_limit:5,roll_sign:1,pitch_sign:1,swap_axes:false}};
  const names={OFF:'실행 안함',LEVEL:'수평 유지',PREDICTIVE:'예측 + 수평 유지'};
  document.querySelectorAll('[data-tray-mode]').forEach(b=>b.classList.toggle('selected',b.dataset.trayMode===profile.mode&&(mode==='preview'||profile.configured)));
  const reportedModes=next.tray?.firmware?.startsWith('APPARENT_V4')?['PWM 유지/꺼짐','Roll 왕복','Pitch 왕복','두 축 왕복','수평 유지','겉보기 중력 정렬','빠른 겉보기 중력 정렬','주행 명령 예측 + 수평']:['PWM 유지/꺼짐','Roll 왕복','Pitch 왕복','두 축 왕복','수평 유지','예측 + 수평'];
  const reported=reportedModes[next.tray?.mode]||'수신 대기';
  $('tray-control-state').textContent=mode==='preview'?`미리보기 · ${names[profile.mode]}`:`요청: ${profile.configured?names[profile.mode]:'미선택'} · 장치: ${next.tray?.connected?reported:'미연결'}`;
  const target=`목표 R ${(profile.roll_target_deg||0).toFixed(2)}° / P ${(profile.pitch_target_deg||0).toFixed(2)}°`;
  $('tray-control-note').textContent=mode==='preview'?`${names[profile.mode]} · 기구학적 표시, 실제 ESP32 제어 아님 · ${target}`:!next.tray?.connected?'ESP32 상태 대기 · 차체는 트레이 보정 없이 조종할 수 있습니다.':profile.mode==='PREDICTIVE'?`${profile.streaming?'예측 목표 전송 중':'대기: 수평 유지 / 주행 시 예측 적용'} · ${target} · 깊이 분석 미사용`:profile.mode==='OFF'?'보정 꺼짐 · 현재 PWM 유지. PWM 해제와 다릅니다.':'ESP32의 IMU 수평 유지 · 실제 모드는 아래 수신 상태에서 확인하세요.';
  if(mode==='real'&&next.tray?.connected){
    if(!profile.configured)$('tray-control-note').textContent='ESP32의 현재 상태를 읽었습니다. 위 버튼으로 사용할 모드를 명시적으로 선택하세요.';
    else if((profile.mode==='OFF'&&next.tray.mode!==0)||(profile.mode!=='OFF'&&![4,next.tray.predictive_mode||5].includes(next.tray.mode)))$('tray-control-note').textContent='모드 요청과 장치의 수신 상태가 아직 다릅니다. 상태 갱신 또는 오류 메시지를 확인하세요.';
  }
  const key=mode+JSON.stringify(profile.parameters);
  if(key!==lastTrayProfile){
    lastTrayProfile=key;const p=profile.parameters;
    $('prediction-lead').value=p.lead;$('prediction-limit').value=p.angle_limit;$('prediction-roll-sign').value=p.roll_sign;$('prediction-pitch-sign').value=p.pitch_sign;$('prediction-swap').checked=p.swap_axes;
  }
  $('firmware-note').textContent=next.tray?.connected?(next.tray.protocol===2?'PROTO=2 앱용 펌웨어 수신 확인 · 예측 목표각 지원':'기존 펌웨어 수신 중 · 수평 유지/미세 조정 가능, 예측은 앱용 펌웨어 업로드 필요'):'실차 예측은 PROTO=2 앱용 펌웨어가 필요합니다. 기존 펌웨어의 수평 유지는 사용할 수 있습니다.';
}

function notice(message, error=false) {
  $('notice-text').textContent = message;
  $('notice').className = `notice ${error ? 'error' : mode === 'real' ? 'real' : ''}`;
  if (error) errorUntil = performance.now() + 5000;
  if(document.body.classList.contains('camera-focus'))requestAnimationFrame(fitCameraView);
}

function config() {
  const angle=Number($('hill-angle').value),length=Number($('hill-length').value),lengthMode=$('hill-length-mode').value;
  const profileLength=lengthMode==='total_base'?length:lengthMode==='slope_surface'?2*length*Math.cos(angle*Math.PI/180):2*length;
  return {speed:Number($('speed').value)/100,distance:Number($('distance').value),
    radius:Number($('radius').value),direction:Number($('direction').value),
    bump_gap:Number($('bump-gap').value),hill_length:length,hill_span:Number($('hill-span').value),
    hill_angle:angle,hill_length_mode:lengthMode,
    bump_width:profileLength,bump_height:profileLength/2*Math.tan(angle*Math.PI/180)};
}

async function request(action, data={}) {
  const response = await fetch(`/api/${action}`,{method:'POST',headers:{'Content-Type':'application/json','X-Hongik-Control':token},body:JSON.stringify(data)});
  const result = await response.json();
  if (!result.ok) throw new Error(result.error || '명령을 처리하지 못했습니다.');
  return result;
}

async function run(kind) {
  if (pending || state?.active) return;
  if (!serverOnline) {notice('화면 서버 연결을 확인해 주세요.',true);return;}
  if (mode === 'real' && !state?.ready) {notice(state?.sensor_reason || '실차 연결 준비를 먼저 눌러 주세요.',true);return;}
  pending = true;
  try {
    const {bump_width,bump_height,...parameters}=config();
    const result = await request('start',{kind,mode,config:parameters,owner,bump_measured:bumpMeasured});
    selected=kind; state=result.state; clearTrail(); updateCourse(); renderState(state);
  } catch(error) {notice(error.message,true);}
  finally {pending=false;}
}

async function stop() {
  clearManual();teleopControl='';
  if (!token) return;
  try {const result=await request('stop'); renderState(result.state);}
  catch(error){notice('중지 요청 전송 실패: 실차에서는 차체 전원을 직접 확인해 주세요.',true);}
}

function setMode(next) {
  if (state?.active) {notice('현재 시험을 먼저 중지해 주세요.',true);return;}
  clearTrail();mode=next;
  $('preview-mode').classList.toggle('selected',mode==='preview');
  $('real-mode').classList.toggle('selected',mode==='real');
  $('preview-mode').setAttribute('aria-pressed',String(mode==='preview'));
  $('real-mode').setAttribute('aria-pressed',String(mode==='real'));
  $('bump-confirm-row').hidden=mode!=='real';
  $('view-mode').textContent=mode==='real'?'● REAL ROBOT':'● 3D PREVIEW';
  $('view-mode').classList.toggle('real',mode==='real');
  $('bump-note').textContent=mode==='real'?'실측한 각도·폭·길이 기준을 확인하고 앞바퀴 출발선에서 1.2m에 정렬하세요. 총 1.3m에서 언덕 진입 후 정지합니다.':'15° · 폭 60cm · 길이 80cm. 길이 기준에 따라 높이를 계산합니다. 주행은 총 1.30m입니다.';
  if(mode==='real') {
    notice(state?.ready?'실차 모드입니다. 시나리오 카드를 누르면 실제 로봇이 움직입니다.':'실차 연결 준비를 누르면 Jetson 센서와 차체 브리지를 연결합니다.');
  } else notice('3D 미리보기 모드입니다. 로봇으로 명령을 보내지 않습니다.');
  updateButtons();
  if(state)renderTrayControl(state);
}

function updateButtons() {
  const follow=state?.follow||{};
  const detectorLive=follow.version===1&&Number.isFinite(follow.source_unix)&&Math.abs(Date.now()/1000-follow.source_unix)<.4&&follow.fault!=='duplicate_publishers';
  $('follow-prepare').disabled=pending||!!state?.active||!!follow.preparing||detectorLive||!state?.connected||!serverOnline;
  $('follow-prepare').textContent=follow.preparing?'검출기 준비 중…':detectorLive?'검출기 실행 중 ✓':'사람 검출 준비';
  $('follow-arm').disabled=pending||!!state?.active||!!follow.preparing||mode!=='real'||!state?.ready||!follow.ready||!serverOnline;
  $('follow-arm').textContent=state?.active&&state.kind==='follow'?(state.follow_waiting?'잠시 정지 · 자동 재개 대기':state.state==='follow_hold'?'거리 유지 중 · 추종 켜짐':'사람 추종 중'):'▶ 이 사람 따라가기 시작';
  $('follow-distance').disabled=!!state?.active||pending;
  const quality=follow.depth_quality;
  const depthText=quality&&Number.isFinite(quality.valid_fraction)?` · 유효 깊이 ${Math.round(quality.valid_fraction*100)}%` : '';
  $('follow-status').textContent=follow.preparing?'검출기 준비 중…':follow.ready?`선택 대상 확인 · 거리 ${Number(follow.distance_m).toFixed(2)}m · 방향 ${(Number(follow.bearing_rad)*180/Math.PI).toFixed(1)}°${depthText}`:follow.reason||'검출 대기';
  if(state?.active&&state.kind==='follow')$('follow-status').textContent=`${state.message}\n${$('follow-status').textContent}`;
  document.querySelectorAll('[data-run]').forEach(button=>{
    button.disabled=!!state?.active || pending || !serverOnline || (mode==='real'&&(!allowReal||!state?.ready));
    button.classList.toggle('active',button.dataset.run===selected);
  });
  $('settings').disabled=!!state?.active;
  $('jetson-host').disabled=!!state?.active||!!state?.connecting||pending;
  $('reset-course').disabled=!!state?.active||!state?.ready;
  const tray=state?.tray||{},trayReady=mode==='real'&&allowReal&&state?.connected&&tray.connected&&!state?.active;
  $('tray-list').disabled=trayPending||!allowReal||!state?.connected||!!state?.active;
  $('tray-connect').disabled=trayPending||!allowReal||!state?.connected||!!state?.active||!$('tray-port').value;
  document.querySelectorAll('[data-tray]').forEach(button=>{
    let allowed=trayReady;
    if(['g','h'].includes(button.dataset.tray))allowed&&=tray.roll_enabled&&tray.pitch_enabled&&tray.imu_fresh;
    if(['v','h'].includes(button.dataset.tray))allowed&&=tray.zero_saved&&tray.imu_fresh;
    if(button.dataset.tray==='h')allowed&&=tray.direction_checked;
    if(['Q','W','E','R'].includes(button.dataset.tray))allowed&&=tray.mode===0&&(button.dataset.tray==='Q'||button.dataset.tray==='W'?tray.roll_enabled:tray.pitch_enabled);
    button.disabled=trayPending||!allowed;
  });
  document.querySelectorAll('[data-drive]').forEach(b=>b.disabled=!!state?.active||pending);
  $('teleop-arm').disabled=pending||!serverOnline||(!!state?.active&&!manualOwned())||(mode==='real'&&(!allowReal||!state?.ready));
  $('teleop-arm').classList.toggle('armed',!!manualOwned());$('teleop-arm').textContent=manualOwned()?'수동 조종 끄기':'수동 조종 켜기';
  document.querySelectorAll('[data-motion]').forEach(b=>b.disabled=!manualOwned());
  $('teleop-status').textContent=manualOwned()?`조종 켜짐 · ${mode==='real'?'실제 로봇':'미리보기'} · 키를 놓으면 정지`:'조종 꺼짐 · 키 입력 대기';
  document.querySelectorAll('[data-tray-mode]').forEach(b=>{
    let ready=mode==='preview'||trayReady;
    if(mode==='real'&&b.dataset.trayMode!=='OFF')ready&&=tray.roll_enabled&&tray.pitch_enabled&&tray.imu_fresh&&tray.zero_saved&&tray.direction_checked;
    if(mode==='real'&&b.dataset.trayMode==='PREDICTIVE')ready&&=tray.protocol===2;
    b.disabled=!!state?.active||trayPending||!serverOnline||!ready;
  });
  $('prediction-apply').disabled=!!state?.active||trayPending||!serverOnline||(mode==='real'&&!trayReady);
  document.querySelectorAll('.prediction-fields input,.prediction-fields select').forEach(input=>input.disabled=!!state?.active||trayPending);
  $('connect').disabled=pending||!!state?.active||!!state?.connecting||!allowReal;
  $('connect').innerHTML=state?.connecting?'연결 준비 중…':state?.ready?'실차 센서 연결됨 <span>✓</span>':state?.connected?'센서 수신 대기 <span>↻</span>':'실차 연결 준비 <span>↗</span>';
}

function renderState(next) {
  state=next;
  if(next.active)selected=next.kind;
  if(lastMode!==next.mode || next.elapsed<lastElapsed-0.5 || lastCourseVersion!==next.course_version) clearTrail();
  lastCourseVersion=next.course_version;
  lastMode=next.mode;lastElapsed=next.elapsed;
  const sensorsRecovered=next.mode==='real'&&next.ready;
  const stoppedMessage=`직전 시험 중단: ${next.message}${sensorsRecovered?' · 현재 센서 수신 정상 · 다시 시작하려면 조종/시험을 선택하세요.':''}`;
  $('run-kind').textContent=next.state==='idle'?'READY TO EXPLORE':`${next.mode==='real'?'REAL ROBOT':'3D PREVIEW'} / ${labels[next.kind]}`;
  $('run-stage').textContent=next.state==='idle'?'시나리오를 선택해 주세요':next.label;
  $('run-stage').classList.toggle('error-text',next.state==='stopped'&&!sensorsRecovered);
  $('run-message').textContent=next.state==='idle'?'왼쪽 카드를 누르면 선택한 시나리오가 시작됩니다.':next.state==='stopped'?stoppedMessage:next.message;
  const seconds=next.state==='idle'?0:next.elapsed;
  $('elapsed').textContent=`${String(Math.floor(seconds/60)).padStart(2,'0')}:${(seconds%60).toFixed(1).padStart(4,'0')}`;
  $('progress').style.width=`${next.progress*100}%`;
  const live=mode==='real'&&next.connected;
  const shown=live?next.robot_pose:null;
  $('metric-v').innerHTML=`${((live?(shown?.v||0):next.v)*100).toFixed(1)}<small>cm/s</small>`;
  $('metric-d').innerHTML=`${(live&&shown?Math.hypot(shown.x,shown.y):next.distance).toFixed(2)}<small>m</small>`;
  $('metric-yaw').innerHTML=`${(live?(shown?.yaw||0)*180/Math.PI:next.kind==='turn'?next.turn_deg:next.pose.yaw*180/Math.PI).toFixed(1)}<small>°</small>`;
  $('metric-source').textContent=mode==='real'?(next.connected?'실차 Odom':'수신 대기'):'미리보기';
  $('sensor-dot').className=next.ready?'good':next.connected?'bad':'';
  $('sensor-label').textContent=next.connected?(next.sensor_reason||(drive==='follow'?'실시간 센서 수신 · 근접·후방 공간 확인 중':'실시간 오도메트리·라이다 수신 중 · 장애물 자동 정지 꺼짐')):'실차 연결 시 실제 센서를 수신합니다.';
  $('countdown').hidden=next.state!=='countdown';
  $('countdown').querySelector('strong').textContent=Math.max(1,Math.ceil(1.5-next.elapsed));
  $('footer-status').textContent=mode==='real'?(next.tray?.connected&&next.tray?.imu_fresh?'실차 · 트레이 BNO055 측정 / 차체 경사 추정 / 유출량 미측정':'실차 · 트레이 IMU 대기 / 차체 경사 추정 / 유출량 미측정'):'미리보기 · 기구학적 동작 예시 / 실제 물리·물 유출량 시뮬레이션 아님';
  if(performance.now()>errorUntil) {
    if(next.state==='stopped')notice(stoppedMessage,!sensorsRecovered);
    else if(next.active)notice(`${next.mode==='real'?'실제 로봇 시험':'3D 미리보기'} · ${labels[next.kind]} · ${next.label}`);
    else if(drive==='follow'&&mode==='real')notice(next.ready?`실차 연결됨 · ${next.follow?.ready?'거리와 검출 대상을 확인한 뒤 추종 시작을 누르세요.':next.follow?.reason||'사람 검출 준비를 눌러 주세요.'}`:next.sensor_reason||'실차 연결 준비를 눌러 주세요.');
  }
  if(lastPhase!==next.state){lastPhase=next.state; updateCourse();}
  updateMedia(next);
  updateTray(next);
  renderTrayControl(next);
  drawChart(next.chart);
  updateButtons();
}

document.querySelectorAll('[data-run]').forEach(b=>b.addEventListener('click',()=>run(b.dataset.run)));
$('stop').addEventListener('click',stop);
$('quick-stop').addEventListener('click',stop);
$('preview-mode').addEventListener('click',()=>setMode('preview'));
$('real-mode').addEventListener('click',()=>setMode('real'));
document.querySelectorAll('[data-drive]').forEach(b=>b.onclick=()=>setDrive(b.dataset.drive));
$('teleop-arm').onclick=armManual;$('teleop-stop').onclick=stop;
$('follow-prepare').onclick=prepareFollow;$('follow-arm').onclick=startFollow;$('follow-stop').onclick=stop;
document.querySelectorAll('[data-motion]').forEach(button=>{
  button.addEventListener('pointerdown',event=>{if(!manualOwned()||event.button!==0)return;event.preventDefault();button.setPointerCapture(event.pointerId);heldPointers.set(event.pointerId,button.dataset.motion);sendManual();});
  const release=event=>{if(heldPointers.delete(event.pointerId)){manualAxes();sendManual();}};
  for(const type of ['pointerup','pointercancel','lostpointercapture'])button.addEventListener(type,release);
});
function manualSpeedChanged(){
  $('teleop-speed-label').textContent=`${$('teleop-speed').value} cm/s`;$('teleop-yaw-label').textContent=`${Number($('teleop-yaw').value).toFixed(2)} rad/s`;
  document.querySelectorAll('[data-speed]').forEach(b=>b.classList.toggle('selected',b.dataset.speed===$('teleop-speed').value));sendManual();
}
$('teleop-speed').oninput=manualSpeedChanged;$('teleop-yaw').oninput=manualSpeedChanged;
document.querySelectorAll('[data-speed]').forEach(b=>b.onclick=()=>{$('teleop-speed').value=b.dataset.speed;manualSpeedChanged();});
document.querySelectorAll('[data-tray-mode]').forEach(b=>b.onclick=()=>selectTray(b.dataset.trayMode));
$('prediction-apply').onclick=()=>selectTray(state?.tray_controls?.[mode]?.mode||'OFF');
$('tray-list').onclick=async()=>{trayPending=true;updateButtons();try{const r=await request('tray-ports');renderState(r.state);}catch(e){notice(e.message,true);}finally{trayPending=false;updateButtons();}};
$('tray-port').onchange=updateButtons;
$('tray-connect').onclick=async()=>{trayPending=true;updateButtons();try{await request('tray-connect',{port:$('tray-port').value});notice('ESP32 상태 조회를 시작했습니다. 서보는 자동으로 켜지지 않습니다.');}catch(e){notice(e.message,true);}finally{trayPending=false;updateButtons();}};
document.querySelectorAll('[data-tray]').forEach(button=>button.onclick=async()=>{
  try{await request('tray-command',{command:button.dataset.tray,mode});notice('트레이 요청을 보냈습니다. 실제 모드·영점·방향 상태로 적용 여부를 확인하세요.');}
  catch(e){notice(e.message,true);}
});
$('reset-course').addEventListener('click',async()=>{
  try{await request('reset-course');clearTrail();bumpMeasured=false;$('bump-confirm').checked=false;notice('정지한 현재 위치와 방향을 출발선으로 등록했습니다. 언덕은 이 출발선에서 1.2m에 맞춰 주세요.');}
  catch(error){notice(error.message,true);}
});
$('connect').addEventListener('click',async()=>{
  pending=true;updateButtons();notice('Jetson 연결을 준비합니다. 기존 SLAM 지도가 있다면 먼저 백업합니다.');
  try {await request('connect',{host:$('jetson-host').value.trim()});notice('실차 연결 준비 완료. 센서 수신 후 실차 시험을 선택하세요.');}
  catch(error){notice(error.message,true);}
  finally{pending=false;updateButtons();}
});
document.querySelectorAll('#settings input, #settings select').forEach(input=>input.addEventListener('input',()=>{
  $('speed-label').innerHTML=`${$('speed').value} <small>cm/s</small>`;
  $('brake-summary').textContent=`직선 ${Number($('distance').value).toFixed(2)}m`;
  $('turn-summary').textContent=`${$('direction').value==='1'?'좌회전':'우회전'} · 반경 ${Number($('radius').value).toFixed(2)}m`;
  $('hill-height').textContent=`${(config().bump_height*100).toFixed(1)}cm`;
  if((input.id.startsWith('bump-')&&input.id!=='bump-confirm')||input.id.startsWith('hill-')){$('bump-confirm').checked=false;bumpMeasured=false;}
  if(input.id==='bump-confirm')bumpMeasured=input.checked;
  updateCourse();
}));
window.addEventListener('keydown',event=>{
  if((event.code==='Space'&&(manualOwned()||!['INPUT','SELECT','TEXTAREA'].includes(document.activeElement?.tagName)))||event.code==='Escape'){
    event.preventDefault();stop();
    return;
  }
  if(manualOwned()&&keyMotion[event.code]&&!['INPUT','SELECT','TEXTAREA'].includes(document.activeElement?.tagName)&&!document.activeElement?.isContentEditable){
    event.preventDefault();if(!event.repeat){heldKeys.add(event.code);sendManual();}
  }
});
window.addEventListener('keyup',event=>{if(heldKeys.delete(event.code)){event.preventDefault();manualAxes();sendManual();}});
window.addEventListener('blur',()=>{if(manualOwned()||(state?.active&&state.kind==='follow'))stop();else clearManual();});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&state?.active&&(state.mode==='real'||manualOwned()))stop();});
window.addEventListener('pagehide',()=>{if(state?.active&&state.mode==='real')fetch('/api/stop',{method:'POST',headers:{'Content-Type':'application/json','X-Hongik-Control':token},body:'{}',keepalive:true}).catch(()=>{});});
setInterval(()=>{if(state?.active&&!document.hidden)request('heartbeat',{owner}).catch(()=>{});},200);
setInterval(()=>{if(manualOwned()&&!document.hidden)sendManual();},100);
setInterval(()=>{if(lastPacket&&performance.now()-lastPacket>1500){serverOnline=false;$('link-dot').className='bad';$('link-label').textContent='화면 연결 끊김';updateButtons();}},500);

// Three.js local scene. Illustrates geometry; never predicts actual spill volume.
const container=$('viewport');
const scene=new THREE.Scene();scene.background=new THREE.Color('#e9eadf');
const camera=new THREE.PerspectiveCamera(39,1,0.01,50);
const renderer=new THREE.WebGLRenderer({antialias:true,alpha:false});
renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));renderer.shadowMap.enabled=true;
renderer.shadowMap.type=THREE.PCFSoftShadowMap;renderer.outputColorSpace=THREE.SRGBColorSpace;
renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.3;
container.appendChild(renderer.domElement);
const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;
controls.maxPolarAngle=Math.PI/2-0.025;controls.minDistance=0.9;controls.maxDistance=7;controls.target.set(0.65,0.08,-0.1);
function resetCamera(top=false){camera.position.set(top?0.65:2.05,top?3.8:1.7,top?0:2.1);controls.target.set(0.65,0.06,-0.1);controls.update();}
resetCamera();$('camera-reset').onclick=()=>resetCamera();$('camera-top').onclick=()=>resetCamera(true);
scene.add(new THREE.HemisphereLight(0xfffcf2,0x78826e,3));
const light=new THREE.DirectionalLight(0xfff5db,4);light.position.set(-1.2,4,2.5);light.castShadow=true;
light.shadow.mapSize.set(2048,2048);Object.assign(light.shadow.camera,{left:-3,right:3,top:3,bottom:-3,near:0.1,far:10});light.shadow.bias=-0.0005;scene.add(light);
const floor=new THREE.Mesh(new THREE.PlaneGeometry(8,7),new THREE.MeshStandardMaterial({color:0xdcdcc9,roughness:0.95}));floor.rotation.x=-Math.PI/2;floor.position.y=-0.005;floor.receiveShadow=true;scene.add(floor);
const grid=new THREE.GridHelper(8,32,0xbac1a8,0xcdd1ba);grid.position.y=0.001;grid.material.transparent=true;grid.material.opacity=0.55;scene.add(grid);
const robot=new THREE.Group();scene.add(robot);
const assembly=new THREE.Group();assembly.rotation.x=-Math.PI/2;assembly.position.y=0.05;robot.add(assembly);
const fallback=new THREE.Mesh(new THREE.BoxGeometry(0.42,0.14,0.28),new THREE.MeshStandardMaterial({color:0x426454,metalness:.35,roughness:.5}));fallback.position.y=.12;fallback.castShadow=true;robot.add(fallback);
const loader=new STLLoader();
const materials={chassis:new THREE.MeshStandardMaterial({color:0xbfc3b8,metalness:.6,roughness:.4}),wheels:new THREE.MeshStandardMaterial({color:0x353d36,roughness:.9}),bracket:new THREE.MeshStandardMaterial({color:0x345b4a,metalness:.45,roughness:.4})};
const meshes=[['d420_chassis_ros_m.stl','chassis'],['d420_wheels_ros_m.stl','wheels'],['d420_v4_bracket_ros_m.stl','bracket']];
Promise.all(meshes.map(([name,material])=>new Promise(resolve=>loader.load(`/static/assets/${name}`,geometry=>{
  const mesh=new THREE.Mesh(geometry,materials[material]);mesh.castShadow=true;mesh.receiveShadow=true;assembly.add(mesh);resolve();
},undefined,()=>resolve())))).then(()=>{if(assembly.children.length)robot.remove(fallback);$('canvas-loading').hidden=true;window.__sceneReady=true;});
const tray=new THREE.Mesh(new THREE.BoxGeometry(.32,.018,.30),new THREE.MeshStandardMaterial({color:0xe9e3ca,metalness:.25,roughness:.6}));tray.position.y=.32;tray.castShadow=true;robot.add(tray);
const trayRig=new THREE.Group();trayRig.position.y=.32;robot.add(trayRig);
robot.remove(tray);tray.position.y=0;trayRig.add(tray);
for(const x of [-.105,.105])for(const z of [-.10,.10]){
  const leg=new THREE.Mesh(new THREE.CylinderGeometry(.009,.009,.10,8),materials.bracket);leg.position.set(x,.26,z);robot.add(leg);
}
const liquids=[];
for(const x of [-.078,.078])for(const z of [-.074,.074]){
  const cup=new THREE.Mesh(new THREE.CylinderGeometry(.038,.031,.105,28,1,true),new THREE.MeshPhysicalMaterial({color:0xf0f6ec,transparent:true,opacity:.25,roughness:.12,metalness:0,side:THREE.DoubleSide,depthWrite:false}));cup.position.set(x,.382,z);robot.add(cup);
  const liquid=new THREE.Mesh(new THREE.CylinderGeometry(.034,.029,.072,28),new THREE.MeshStandardMaterial({color:0xa8733c,roughness:.35,metalness:.05}));liquid.position.set(x,.365,z);robot.add(liquid);liquids.push(liquid);
  const rim=new THREE.Mesh(new THREE.TorusGeometry(.037,.0016,8,40),new THREE.MeshStandardMaterial({color:0xe7e9de,metalness:.3,roughness:.2}));rim.rotation.x=Math.PI/2;rim.position.set(x,.434,z);robot.add(rim);
  for(const item of [cup,liquid,rim]){robot.remove(item);item.position.y-=.32;trayRig.add(item);}
}
const course=new THREE.Group();scene.add(course);
const trajectory=new THREE.Line(new THREE.BufferGeometry(),new THREE.LineBasicMaterial({color:0xae7f3f,transparent:true,opacity:.8}));scene.add(trajectory);
let trail=[];
function clearTrail(){trail=[];trajectory.geometry.dispose();trajectory.geometry=new THREE.BufferGeometry();}
let lastPorts='';
function updateTray(value){
  const data=value.tray||{},connected=!!data.connected,valid=connected&&data.imu_fresh;
  $('tray-link').textContent=connected?(valid?'실측 상태 수신 중':'USB 연결 · IMU 미준비'):'ESP32 미연결 / 지연';
  $('tray-short').textContent=valid?'BNO055 수신 중':'미연결 / 대기';
  $('tray-roll').textContent=valid&&Number.isFinite(data.roll_deg)?`${data.roll_deg.toFixed(2)}°`:'—';
  $('tray-pitch').textContent=valid&&Number.isFinite(data.pitch_deg)?`${data.pitch_deg.toFixed(2)}°`:'—';
  const trayModes=data.firmware?.startsWith('APPARENT_V4')?['PWM 유지/수동','Roll 왕복','Pitch 왕복','두 축 왕복','IMU 수평 유지','겉보기 중력 정렬','빠른 겉보기 중력 정렬','주행 명령 예측 + IMU']:['PWM 유지/수동','Roll 왕복','Pitch 왕복','두 축 왕복','IMU 수평 제어','예측 목표 + IMU'];
  $('tray-mode').textContent=connected?trayModes[data.mode]||'대기':'미연결';
  $('tray-checks').textContent=connected?`${data.zero_saved?'완료':'미등록'} / ${data.direction_checked?'완료':'미확인'}`:'— / —';
  $('tray-detail').textContent=connected?`PWM 명령 R ${data.roll_pwm}µs / P ${data.pitch_pwm}µs · ${data.zero_saved?'영점 대비 측정':'영점 미등록: 3D 자세 적용 대기'} · ${data.error||(!valid?'IMU 데이터 미준비':'상태 출력 약 2Hz')}`:data.error||'ESP32를 Jetson USB에 연결하고 Arduino 시리얼 모니터는 닫아 주세요. 연결 시에는 상태만 조회합니다.';
  const ports=value.tray_ports||[],key=JSON.stringify(ports);
  if(key!==lastPorts){
    lastPorts=key;const select=$('tray-port'),previous=select.value;select.replaceChildren(new Option('실제 ESP32 USB 장치를 선택하세요',''));
    for(const item of ports){const vid=(item.vid||0).toString(16).padStart(4,'0'),pid=(item.pid||0).toString(16).padStart(4,'0');const option=new Option(`${item.reserved?'[차체/라이다 예약] ':''}${item.description} · ${vid}:${pid} · ${item.port}`,item.port);option.disabled=item.reserved;select.add(option);}
    if(ports.some(p=>p.port===previous&&!p.reserved))select.value=previous;
  }
}
const scanGeometry=new THREE.BufferGeometry();const scanCloud=new THREE.Points(scanGeometry,new THREE.PointsMaterial({color:0x618d9c,size:.013,transparent:true,opacity:.55}));scene.add(scanCloud);
const mapRoot=new THREE.Group();scene.add(mapRoot);mapRoot.visible=false;
const mapMaterial=new THREE.MeshBasicMaterial({transparent:true,opacity:.65,depthWrite:false,side:THREE.DoubleSide});
const mapPlane=new THREE.Mesh(new THREE.PlaneGeometry(1,1),mapMaterial);mapPlane.rotation.x=-Math.PI/2;mapRoot.add(mapPlane);
let lastMapSeq=-1,mapImage=null,mapImageKey='',cameraSeq=-1,cameraLoading=false;
$('camera-image').onload=()=>{cameraLoading=false;};
$('camera-image').onerror=()=>{cameraLoading=false;$('camera-image').hidden=true;$('camera-placeholder').hidden=false;};

function updateMedia(value){
  const follow=value.follow||{},overlay=$('follow-boxes');
  overlay.replaceChildren();overlay.hidden=drive!=='follow'||!value.camera?.available||!Number.isFinite(follow.source_unix)||Math.abs(Date.now()/1000-follow.source_unix)>.5;
  if(!overlay.hidden&&follow.width>0&&follow.height>0){
    overlay.setAttribute('viewBox',`0 0 ${follow.width} ${follow.height}`);
    for(const [index,person] of (follow.people||[]).entries()){
      if(!Array.isArray(person.box)||person.box.length!==4||!person.box.every(Number.isFinite))continue;
      const [x,y,w,h]=person.box,rect=document.createElementNS('http://www.w3.org/2000/svg','rect');
      for(const [key,val] of Object.entries({x,y,width:w,height:h}))rect.setAttribute(key,String(val));
      overlay.append(rect);
      const text=document.createElementNS('http://www.w3.org/2000/svg','text');text.setAttribute('x',String(x));text.setAttribute('y',String(Math.max(20,y-6)));
      const chosen=follow.selected_indices?.includes(index);
      text.textContent=`${chosen?'선택 대상':'사람'} ${Math.round(person.confidence*100)}%${chosen&&follow.ready?' · 추종 가능':''}`;overlay.append(text);
    }
    for(const bounds of follow.depth_quality?.regions||[]){
      if(!Array.isArray(bounds)||bounds.length!==4||!bounds.every(Number.isFinite))continue;
      const [x,y,w,h]=bounds,rect=document.createElementNS('http://www.w3.org/2000/svg','rect');
      for(const [key,val] of Object.entries({x,y,width:w,height:h}))rect.setAttribute(key,String(val));
      rect.classList.add('depth-region');overlay.append(rect);
    }
  }
  const cameraInfo=value.camera||{};
  const available=value.connected&&cameraInfo.available;
  $('camera-status').textContent=available?`${cameraInfo.width}×${cameraInfo.height} · ${(cameraInfo.age||0).toFixed(1)}s 전`:value.connected?'RGB 수신 대기':'미연결';
  $('camera-placeholder').hidden=!!available;$('camera-image').hidden=!available;
  if(available&&cameraSeq!==cameraInfo.seq&&!cameraLoading){
    cameraLoading=true;cameraSeq=cameraInfo.seq;$('camera-image').src=`/api/camera.jpg?seq=${cameraInfo.seq}&t=${Date.now()}`;
  }
  const grid=value.lidar_map;
  $('map-status').textContent=grid?`SLAM · ${(grid.resolution*100).toFixed(0)}cm/셀`:'/map 대기';
  $('map-placeholder').hidden=!!grid;
  mapRoot.visible=!!grid&&mode==='real';
  if(grid){
    mapRoot.position.set(grid.x,.006,-grid.y);mapRoot.rotation.y=grid.yaw;
    mapPlane.scale.set(grid.width,grid.height,1);mapPlane.position.set(grid.width/2,0,-grid.height/2);
    const key=`${value.course_version}:${grid.seq}`;
    if(key!==mapImageKey){
      mapImageKey=key;const image=new Image();image.onload=()=>{
        if(mapImageKey!==key)return;mapImage=image;
        mapMaterial.map?.dispose();mapMaterial.map=new THREE.Texture(image);
        mapMaterial.map.colorSpace=THREE.SRGBColorSpace;mapMaterial.map.needsUpdate=true;mapMaterial.needsUpdate=true;
        drawLidar(value);
      };image.src=grid.image;
    }
  }
  drawLidar(value);
}

function drawLidar(value){
  const canvas=$('lidar-map'),ratio=window.devicePixelRatio||1;
  const width=canvas.clientWidth,height=155;
  if(canvas.width!==Math.round(width*ratio)||canvas.height!==height*ratio){canvas.width=Math.round(width*ratio);canvas.height=height*ratio;}
  const ctx=canvas.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,width,height);
  ctx.fillStyle='#eff0e6';ctx.fillRect(0,0,width,height);
  const grid=value.lidar_map,pose=value.robot_pose;
  if(!grid||!mapImage)return;
  const scale=Math.min((width-18)/grid.width,(height-12)/grid.height);
  const w=grid.width*scale,h=grid.height*scale,left=(width-w)/2,top=(height-h)/2;
  ctx.drawImage(mapImage,left,top,w,h);
  if(pose){
    const dx=pose.x-.135-grid.x,dy=pose.y-grid.y,c=Math.cos(grid.yaw),s=Math.sin(grid.yaw);
    const x=c*dx+s*dy,y=-s*dx+c*dy;
    ctx.save();ctx.translate(left+x*scale,top+h-y*scale);ctx.rotate(-(pose.yaw-grid.yaw));
    ctx.fillStyle='#b27337';ctx.beginPath();ctx.moveTo(7,0);ctx.lineTo(-5,-4);ctx.lineTo(-3,0);ctx.lineTo(-5,4);ctx.closePath();ctx.fill();ctx.restore();
  }
}

function disposeCourse(){for(const child of [...course.children]){child.geometry?.dispose();if(Array.isArray(child.material))child.material.forEach(m=>m.dispose());else child.material?.dispose();course.remove(child);}}
function courseLabel(text,x,z){
  const canvas=document.createElement('canvas');canvas.width=512;canvas.height=96;
  const ctx=canvas.getContext('2d');ctx.fillStyle='#395643';ctx.font='30px sans-serif';ctx.textAlign='center';ctx.fillText(text,256,58);
  const texture=new THREE.CanvasTexture(canvas);const label=new THREE.Sprite(new THREE.SpriteMaterial({map:texture,transparent:true,depthTest:false}));
  label.scale.set(.43,.08,1);label.position.set(x,.065,z);course.add(label);
}
function updateCourse(){
  disposeCourse();
  const cfg=state?.active?state.config:config();const kind=state?.active?state.kind:selected;
  const offset=.135,pts=[];
  pts.push(new THREE.Vector3(-offset,.016,0),new THREE.Vector3(1-offset,.016,0));
  if(kind==='turn'){
    for(let i=0;i<=40;i++){const a=i/40*Math.PI/2;pts.push(new THREE.Vector3(1-offset+cfg.radius*Math.sin(a),.016,-cfg.direction*cfg.radius*(1-Math.cos(a))));}
  }else if(kind==='bump')pts.push(new THREE.Vector3(1.30-offset,.016,0));
  const route=new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),new THREE.LineDashedMaterial({color:0x638664,dashSize:.055,gapSize:.035,transparent:true,opacity:.8}));route.computeLineDistances();course.add(route);
  for(const [x,color]of [[0,0x52745a],[1,0xb2854d],[1.2,0x98855c],[1.3,0xb87247]]){
    const line=new THREE.Mesh(new THREE.PlaneGeometry(.014,.95),new THREE.MeshBasicMaterial({color,side:THREE.DoubleSide,transparent:true,opacity:.55}));line.rotation.x=-Math.PI/2;line.position.set(x,.01,0);course.add(line);
  }
  courseLabel('START 0.00m',0,.59);courseLabel('1.00m',1,.57);courseLabel('HILL 1.20m',1.2,-.61);courseLabel('STOP 1.30m',1.57,.57);
  for(const z of [-.48,.48]){
    const lane=new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-.5,.009,z),new THREE.Vector3(Math.max(2.6,1.4+cfg.bump_width),.009,z)]),new THREE.LineBasicMaterial({color:0xb8bba5,transparent:true,opacity:.6}));course.add(lane);
  }
  const shape=new THREE.Shape();shape.moveTo(0,0);shape.lineTo(cfg.bump_width/2,cfg.bump_height);shape.lineTo(cfg.bump_width,0);shape.closePath();
  const opacity=kind==='bump'?1:.38;
  const hill=new THREE.Mesh(new THREE.ExtrudeGeometry(shape,{depth:cfg.hill_span,bevelEnabled:false,steps:1}),new THREE.MeshStandardMaterial({color:0xb99b6b,roughness:.9,transparent:opacity<1,opacity}));hill.position.set(1.2,.003,-cfg.hill_span/2);hill.castShadow=true;hill.receiveShadow=true;course.add(hill);
  const edges=new THREE.LineSegments(new THREE.EdgesGeometry(hill.geometry),new THREE.LineBasicMaterial({color:0x927950,transparent:true,opacity:.6}));edges.position.copy(hill.position);course.add(edges);
  const slope=Math.atan2(cfg.bump_height,cfg.bump_width/2);
  for(const sign of [1,-1])for(const z of [-cfg.hill_span*.32,cfg.hill_span*.32]){
    const stripe=new THREE.Mesh(new THREE.BoxGeometry(Math.hypot(cfg.bump_width/2,cfg.bump_height)*.55,.003,.045),new THREE.MeshStandardMaterial({color:0x555445,transparent:true,opacity}));
    stripe.position.set(1.2+cfg.bump_width*(sign===1?.25:.75),cfg.bump_height*.5+.007,z);stripe.rotation.z=sign*slope;course.add(stripe);
  }
}
updateCourse();

function heightAt(x,cfg){const t=(x-1.2)/cfg.bump_width;return t>0&&t<1?cfg.bump_height*(1-Math.abs(2*t-1)):0;}

let lastV=0,filteredAccel=0,prevTime=performance.now();
function frame(time){
  requestAnimationFrame(frame);
  const dt=Math.min(.05,(time-prevTime)/1000);prevTime=time;
  if(state){
    const real=mode==='real';const p=real?(state.robot_pose||{x:0,y:0,yaw:0}):state.pose;
    const bodyX=p.x-.135;robot.position.x=THREE.MathUtils.lerp(robot.position.x,bodyX,.3);robot.position.z=THREE.MathUtils.lerp(robot.position.z,-p.y,.3);
    robot.rotation.y=p.yaw;
    let pitch=0,elevation=0;
    if((state.active?state.kind:selected)==='bump'&&Math.abs(p.y)<(state.active?state.config:config()).hill_span/2){
      const cfg=state.active?state.config:config();
      const front=heightAt(bodyX+.135*Math.cos(p.yaw),cfg),rear=heightAt(bodyX-.135*Math.cos(p.yaw),cfg);pitch=Math.atan2(front-rear,.27);elevation=(front+rear)*.5;
    }
    robot.position.y=elevation;robot.rotation.z=pitch;
    const measured=state.tray||{};
    if(mode==='real'&&measured.connected&&measured.imu_fresh&&measured.zero_saved){
      const qYaw=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0),p.yaw);
      const qMeasured=new THREE.Quaternion().setFromEuler(new THREE.Euler(measured.roll_deg*Math.PI/180,0,-measured.pitch_deg*Math.PI/180,'XYZ'));
      trayRig.quaternion.copy(robot.quaternion.clone().invert().multiply(qYaw.multiply(qMeasured)));
    }else if(mode==='preview'&&state.tray_controls?.preview?.mode!=='OFF'){
      const profile=state.tray_controls?.preview;
      const angleR=profile?.mode==='PREDICTIVE'?(profile.roll_target_deg||0):0;
      const angleP=profile?.mode==='PREDICTIVE'?(profile.pitch_target_deg||0):0;
      const qYaw=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0),p.yaw);
      const qTilt=new THREE.Quaternion().setFromEuler(new THREE.Euler(angleR*Math.PI/180,0,-angleP*Math.PI/180,'XYZ'));
      trayRig.quaternion.copy(robot.quaternion.clone().invert().multiply(qYaw.multiply(qTilt)));
    }else trayRig.quaternion.identity();
    filteredAccel=THREE.MathUtils.lerp(filteredAccel,(state.v-lastV)/Math.max(dt,.01),.03);lastV=state.v;
    for(const liquid of liquids){liquid.rotation.z=mode==='preview'?THREE.MathUtils.clamp(-filteredAccel/9.81,-.10,.10):0;}
    if((state.active||(real&&state.robot_pose))&&(trail.length===0||new THREE.Vector3(bodyX,.018,-p.y).distanceTo(trail.at(-1))>.01)){
      trail.push(new THREE.Vector3(bodyX,.018,-p.y));trajectory.geometry.dispose();trajectory.geometry=new THREE.BufferGeometry().setFromPoints(trail);
    }
    const raw=real&&state.connected?state.scan_points:[];const vertices=[];
    for(const [x,y] of raw){vertices.push(bodyX+Math.cos(p.yaw)*x-Math.sin(p.yaw)*y,.04,-(p.y+Math.sin(p.yaw)*x+Math.cos(p.yaw)*y));}
    scanGeometry.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));scanGeometry.computeBoundingSphere();
  }
  controls.update();if(!document.body.classList.contains('camera-focus'))renderer.render(scene,camera);
}
requestAnimationFrame(frame);
new ResizeObserver(()=>{const width=container.clientWidth,height=container.clientHeight;if(!width||!height)return;renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();}).observe(container);

function drawChart(rows=[]){
  const canvas=$('chart');const ratio=window.devicePixelRatio||1;const width=canvas.clientWidth,height=70;
  if(!width)return;
  if(canvas.width!==Math.round(width*ratio)||canvas.height!==height*ratio){canvas.width=Math.round(width*ratio);canvas.height=height*ratio;}
  const ctx=canvas.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,width,height);
  ctx.lineWidth=1;ctx.strokeStyle='#dce0d3';for(const y of [12,37,62]){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(width,y);ctx.stroke();}
  ctx.fillStyle='#9ba18e';ctx.font='8px system-ui';ctx.fillText('30cm/s',2,9);ctx.fillText('0',2,69);
  if(!rows.length)return;
  const start=rows[0].t,end=Math.max(start+1,rows.at(-1).t);
  for(const [key,color]of [['cmd_v','#b99d76'],['v','#486950']]){ctx.beginPath();ctx.strokeStyle=color;ctx.lineWidth=1.5;rows.forEach((r,i)=>{const x=35+(r.t-start)/(end-start)*(width-40),y=62-r[key]/.30*49;i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();}
}

function openSocket(){
  const ws=new WebSocket(`ws://${location.host}/ws`);
  ws.onopen=async()=>{try{const r=await fetch('/api/session');const v=await r.json();token=v.token;allowReal=v.allow_real;}catch(e){}};
  ws.onmessage=event=>{lastPacket=performance.now();serverOnline=true;$('link-dot').className='good';$('link-label').textContent='로컬 콘솔 연결됨';renderState(JSON.parse(event.data));};
  ws.onclose=()=>{serverOnline=false;updateButtons();setTimeout(openSocket,1500);};
  ws.onerror=()=>ws.close();
}
try{
  const response=await fetch('/api/session');const session=await response.json();token=session.token;allowReal=session.allow_real;
  $('jetson-host').value=session.host||'seo@192.168.0.47';
  openSocket();updateButtons();
  const view=new URLSearchParams(location.search);
  if(['scenario','teleop','follow'].includes(view.get('drive')))setDrive(view.get('drive'));
  if(view.get('mode')==='real')setMode('real');
  if(view.get('camera')==='full')setCameraView(true);
  if(!allowReal)$('connect').title='실차 기능을 쓰려면 start.sh --allow-real 로 실행하세요.';
}catch(error){notice('로컬 서버에 연결하지 못했습니다. start.sh 실행 상태를 확인해 주세요.',true);}
window.__lab={run,stop,setMode,setDrive,armManual,selectTray,getState:()=>state,config,
  getOperatorStatus:()=>({owned:!!manualOwned(),keys:[...heldKeys],pointers:[...heldPointers.values()],focused:document.activeElement?.id,hidden:document.hidden}),
  getVisual:()=>({x:robot.position.x,z:robot.position.z,yaw:robot.rotation.y,
                  mapVisible:mapRoot.visible,mapTexture:!!mapMaterial.map,
                  cameraWidth:$('camera-image').naturalWidth,
                  trayWorldUp:new THREE.Vector3(0,1,0).applyQuaternion(trayRig.getWorldQuaternion(new THREE.Quaternion())).toArray()})};
