import {rvizViewer} from './rviz.js';
import {updateStopNotice} from './stop_notice.js';
import {experimentMatrix,matrixState} from './experiment_matrix.js';
const $=id=>document.getElementById(id),owner=crypto.randomUUID();
let token='',state=null,run=null,pending=false,selection=null,planned='',image=null,imageKey='',view=null;
let control='',sequence=0,owns=false,sending=false,cameraSeq=-1,cameraLoading=false,arming=false,armEpoch=0,localRouteOwned=false;
const keys=new Set(),pointers=new Map(),keyMap={KeyW:'forward',ArrowUp:'forward',KeyS:'backward',ArrowDown:'backward',KeyA:'left',ArrowLeft:'left',KeyD:'right',ArrowRight:'right'};
const focusDrive=()=>$('drive-focus').focus({preventScroll:true});
const viewer=rvizViewer({getToken:()=>token,request:(...args)=>request(...args),onFocus:focusDrive});
function notice(message,error=false){$('notice').textContent=message;$('notice').classList.toggle('error',error);}
async function request(action,data={}){
  const r=await fetch('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Hongik-Control':token},body:JSON.stringify(data)});
  if(r.status===403){token=(await(await fetch('/api/session')).json()).token;throw Error('연결을 갱신했습니다. 버튼을 다시 눌러 주세요.');}
  const d=await r.json();if(!d.ok)throw Error(d.error||'요청 실패');return d;
}
async function task(kind,data={}){
  if(pending)return;pending=true;buttons();
  try{const r=await request('navigation/'+kind,data);state=r.state;notice(state.message);render();return r;}
  catch(e){notice(e.message,true);}finally{pending=false;buttons();}
}
function activeMap(){return state?.maps.find(m=>m.id===state.current_map);}
function buttons(){
  const busy=pending||state?.busy,active=!!state?.active,ready=state?.ready;
  for(const id of ['mark-start','mark-turn','mark-finish'])$(id).disabled=arming||busy||!ready||!state?.mapping_mode||(active&&state?.kind!=='teleop');
  for(const id of ['connect','save-map','save-point','delete-point','prepare-nav','plan','go'])$(id).disabled=busy||active;
  $('nav-speed').disabled=busy||active;
  $('depth-prepare').disabled=busy||active;
  $('depth-go').disabled=busy||active||!run?.depth_route?.ready;
  $('depth-trial').disabled=$('depth-go').disabled;
  matrixState(busy||active||!run?.depth_route?.ready||!run?.tray?.connected);
  $('experiment-repeat').disabled=busy||active;
  for(const id of ['depth-first','depth-extra','depth-second','depth-speed','depth-brake','depth-tray','depth-lead','depth-angle'])$(id).disabled=busy||active;
  $('manual').disabled=arming||busy||(!active&&!state?.manual_ready)||(!control&&active)||!state?.mapping_mode;
  $('manual').textContent=control&&active?'수동 조종 끄기 · R':'수동 조종 켜기 · R';
  $('keyboard-status').textContent=control&&active?(run?.neutral_required?'일시 정지 · 키를 모두 놓았다 다시 눌러 주세요':'조종 켜짐 · WASD / 방향키 · 놓으면 정지'):'R: 조종 켜기 · Space/Esc: 정지';
  $('drive-focus').classList.toggle('armed',!!control&&active);
  viewer.setLocked(active);$('rviz-interact').disabled=active||$('rviz-container').hidden;
  $('save-map').disabled||=!ready;
  $('save-point').disabled||=!ready||!selection;
  $('prepare-nav').disabled||=!ready||!activeMap()?.remote_path||state?.nav.ready;
  for(const id of ['delete-point','plan','go'])$(id).disabled||=!$('points').value;
  $('plan').disabled||=!ready||!activeMap()?.remote_path||!state?.nav.ready;
  $('go').disabled||=!ready||!activeMap()?.remote_path||!state?.nav.ready||planned!==$('points').value;
}
function highlight(){const a=axes();for(const b of document.querySelectorAll('[data-motion]'))b.classList.toggle('held',({forward:a.forward>0,backward:a.forward<0,left:a.turn>0,right:a.turn<0})[b.dataset.motion]);}
async function stop(){armEpoch++;localRouteOwned=false;keys.clear();pointers.clear();control='';owns=false;highlight();try{const r=await request('stop');if(state)state.active=r.state.active;}catch(e){notice(e.message,true);}buttons();}
function axes(){const a=new Set([...keys].map(k=>keyMap[k]).concat([...pointers.values()]));return {forward:Number(a.has('forward'))-Number(a.has('backward')),turn:Number(a.has('left'))-Number(a.has('right'))};}
async function send(){
  if(!control||!state?.active||document.hidden||sending)return;sending=true;
  try{await request('teleop-input',{...axes(),owner,control_id:control,sequence:++sequence,speed:Number($('speed').value)/100,yaw_speed:Number($('yaw-speed').value)/100});}
  catch(e){notice(e.message,true);await stop();}finally{sending=false;}
}
async function armManual(){
  if(arming||$('manual').disabled)return;
  if(control&&state?.active){await stop();return;}
  arming=true;const epoch=armEpoch;buttons();
  try{const r=await request('teleop-start',{mode:'real',owner,config:{speed:Number($('speed').value)/100}});if(epoch!==armEpoch||document.hidden){await stop();return;}control=r.state.control_id;sequence=0;owns=true;state.active=true;viewer.setLocked(true);focusDrive();await send();}
  catch(e){notice(e.message,true);}finally{arming=false;buttons();}
}
$('manual').onclick=armManual;
for(const b of document.querySelectorAll('[data-motion]')){
  b.onpointerdown=e=>{if(!control)return;e.preventDefault();focusDrive();b.setPointerCapture(e.pointerId);pointers.set(e.pointerId,b.dataset.motion);highlight();send();};
  for(const event of ['pointerup','pointercancel','lostpointercapture'])b.addEventListener(event,e=>{pointers.delete(e.pointerId);highlight();send();});
}
window.addEventListener('keydown',e=>{
  const editing=['INPUT','SELECT','TEXTAREA'].includes(document.activeElement?.tagName);
  if(e.code==='Escape'||(e.code==='Space'&&!['INPUT','SELECT'].includes(document.activeElement?.tagName))){e.preventDefault();stop();return;}
  if(e.code==='KeyR'&&!editing){e.preventDefault();if(!e.repeat)armManual();return;}
  if(control&&keyMap[e.code]&&!editing){e.preventDefault();keys.add(e.code);highlight();send();}
});
window.addEventListener('keyup',e=>{if(keys.delete(e.code)){highlight();send();}});
window.addEventListener('blur',()=>{if(owns&&state?.active&&!localRouteOwned)stop();});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&owns&&state?.active&&!localRouteOwned)stop();});
window.addEventListener('pagehide',()=>{if(owns&&state?.active&&!localRouteOwned)fetch('/api/stop',{method:'POST',headers:{'Content-Type':'application/json','X-Hongik-Control':token},body:'{}',keepalive:true}).catch(()=>{});});
$('stop').onclick=stop;$('pad-stop').onclick=stop;
$('speed').oninput=()=>{$('speed-text').textContent=$('speed').value+' cm/s';send();};
$('speed').addEventListener('pointerup',()=>{if(control)focusDrive();});
$('yaw-speed').oninput=()=>{$('yaw-speed-text').textContent=(Number($('yaw-speed').value)/100).toFixed(2)+' rad/s';send();};
$('yaw-speed').addEventListener('pointerup',()=>{if(control)focusDrive();});
for(const b of document.querySelectorAll('[data-speed]'))b.onclick=()=>{$('speed').value=b.dataset.speed;$('speed').oninput();focusDrive();};
function showView(rviz){$('rviz-container').hidden=!rviz;$('map').hidden=rviz;$('show-rviz').setAttribute('aria-pressed',rviz);$('show-map').setAttribute('aria-pressed',!rviz);buttons();draw();if(control)focusDrive();}
$('show-rviz').onclick=()=>showView(true);$('show-map').onclick=()=>showView(false);
$('rviz-interact').onclick=()=>viewer.toggle();
$('connect').onclick=()=>task('connect',{host:$('host').value});
$('save-map').onclick=()=>task('save-map',{name:$('map-name').value});
$('save-point').onclick=()=>task('save-point',{map_name:$('map-name').value,name:$('point-name').value,point:{...selection,yaw:Number($('yaw').value)*Math.PI/180}});
for(const role of ['start','turn','finish'])$('mark-'+role).onclick=async()=>{await task('mark-current',{role,map_name:$('map-name').value});if(control)focusDrive();};
$('delete-point').onclick=()=>task('delete-point',{id:$('points').value});
$('prepare-nav').onclick=()=>task('prepare-nav');
$('plan').onclick=async()=>{if(await task('plan',{id:$('points').value})){planned=$('points').value;buttons();}};
$('depth-prepare').onclick=()=>task('depth-prepare');
async function startDepthRoute(trial,condition=null,speed=null){
  if(pending||state?.active)return;
  const epoch=armEpoch;pending=true;buttons();
  try{const r=await request('depth-route-start',{mode:'real',owner,trial,experiment_case:condition?.id,repeat:Number($('experiment-repeat').value),tray_level:true,brake:condition?true:$('depth-brake').checked,tray_predictive:$('depth-tray').checked,tray_prediction:{lead:Number($('depth-lead').value)/1000,angle_limit:Number($('depth-angle').value)},first:Number($('depth-first').value),turn_extension:Number($('depth-extra').value)/100,second:Number($('depth-second').value),config:{speed:(speed??Number($('depth-speed').value))/100}});
    if(epoch!==armEpoch){await stop();return;}
    owns=r.state.active;localRouteOwned=!!r.state.active;control='';if(state)state.active=r.state.active;notice(r.state.message);focusDrive();
  }catch(e){notice(e.message,true);}finally{pending=false;buttons();}
}
$('depth-go').onclick=()=>startDepthRoute(false);
$('depth-trial').onclick=()=>startDepthRoute(true);
experimentMatrix((condition,speed)=>startDepthRoute(false,condition,speed));
$('nav-speed').oninput=()=>{$('nav-speed-text').textContent=$('nav-speed').value+' cm/s';};
$('go').onclick=async()=>{if(await task('go',{id:$('points').value,owner,speed:Number($('nav-speed').value)/100})){owns=true;control='';}};
$('points').onchange=()=>{planned='';buttons();draw();};
$('yaw').oninput=draw;
$('camera').onload=()=>{cameraLoading=false;};$('camera').onerror=()=>{cameraLoading=false;};
function render(){
  if(!state.active){control='';owns=false;localRouteOwned=false;keys.clear();pointers.clear();highlight();}
  $('run-status').textContent=state.active?state.run_message:state.sensor_reason||'정지 · '+state.run_message;
  $('map-status').textContent=state.map?`${(state.map.resolution*100).toFixed(0)}cm/셀 · ${state.map.width.toFixed(1)}×${state.map.height.toFixed(1)}m`:state.reason;
  const robot=state.map?.robot;
  $('pose').textContent=robot?`X ${robot.x.toFixed(2)}m · Y ${robot.y.toFixed(2)}m · 방향 ${(robot.yaw*180/Math.PI).toFixed(0)}°`:state.reason;
  $('nav-status').textContent=state.nav.ready?`지점 이동 준비됨${Number.isFinite(state.nav.remaining)?' · 남은 거리 '+state.nav.remaining.toFixed(2)+'m':''}`:'지점 이동 준비 전';
  const current=activeMap();$('map-record').textContent=`저장 지도 ${state.maps.filter(m=>m.remote_path).length}개${current?' · 현재: '+current.name+(current.remote_path?' · 지도 저장됨':' · 지도 저장 전 / 지점 기록 중'):' · 현재 지도 저장 전'}`;
  const selected=$('points').value,points=current?.points||[],key=JSON.stringify(points);
  $('draft-points').textContent=points.length?'기록: '+points.map(p=>p.name).join(' → '):'아직 기록한 지점 없음';
  if($('points').dataset.key!==key){
    $('points').dataset.key=key;$('points').replaceChildren();
    for(const p of points){const o=document.createElement('option');o.value=p.id;o.textContent=p.name;$('points').append(o);}
    if(!points.length){const o=document.createElement('option');o.value='';o.textContent='지점을 저장해 주세요';$('points').append(o);}
    if(points.some(p=>p.id===selected))$('points').value=selected;
  }
  if(state.map&&imageKey!==state.map.session+':'+state.map.seq){
    const key=state.map.session+':'+state.map.seq;imageKey=key;const next=new Image();next.onload=()=>{if(imageKey===key){image=next;draw();}};next.src=state.map.image;
  }
  if(run){
    updateStopNotice(run);
    const rec=run.recording||{};
    const tray=run.tray||{};$('route-tray-link').textContent=tray.connected?`ESP32 연결 · ${tray.imu_fresh?'IMU 수신':'IMU 미준비'} · R ${Number.isFinite(tray.roll_deg)?tray.roll_deg.toFixed(2)+'°':'미확인'} / P ${Number.isFinite(tray.pitch_deg)?tray.pitch_deg.toFixed(2)+'°':'미확인'}`:'ESP32 미연결 · 15조건 비교 실험 전 주행·트레이 제어실에서 USB 목록 → ESP32 연결 및 영점·방향 확인이 필요합니다.';
    $('experiment-recording').textContent=rec.error?'기록 오류 · '+rec.error:rec.run_id?`자동 기록 중 · ${rec.samples||0}개 · ${rec.run_id}`:'실험 시작 시 자동 기록 · 주행 단계·속도·각도·정지 사유 저장';
    const ages=run.depth_diagnostics||{},age=ages.source_unix_age_ms;
    $('depth-status').textContent=run.kind==='depth-route'?`${run.message||run.phase||''}${run.depth_paused?' · 새 영상 '+(run.depth_recovery_frames||0)+'/3':''}${Number.isFinite(age)?' · 깊이 지연 '+Math.round(age)+'ms':''}${run.brake_done?' · 중간 급정지 완료':''}`:run.depth_route?.ready?'전방 깊이 수신 중 · 거리 입력 후 시작':run.depth_route?.reason||'깊이 관측 대기';
    const prediction=run.tray_prediction;
    $('depth-tray-status').textContent=prediction?.enabled?`Jetson 예측 연동 · ${prediction.source||'출발 확인 중'} · 목표 R ${(prediction.roll_deg??0).toFixed(2)}° / P ${(prediction.pitch_deg??0).toFixed(2)}°`:'트레이 예측 미선택 · 기존 수평 유지 모드는 별도로 설정';
    const camera=run.camera;$('camera-status').textContent=camera?.available?`${camera.width}×${camera.height}`:'수신 대기';
    $('camera').hidden=!camera?.available;$('camera-wait').hidden=!!camera?.available;
    if(camera?.available&&camera.seq!==cameraSeq&&!cameraLoading){cameraLoading=true;cameraSeq=camera.seq;$('camera').src='/api/camera.jpg?seq='+camera.seq;}
  }
  buttons();draw();
}
function worldToCanvas(x,y){const g=state.map,dx=x-g.x,dy=y-g.y,c=Math.cos(g.yaw),s=Math.sin(g.yaw);return [view.left+(c*dx+s*dy)*view.scale,view.top+view.h-(-s*dx+c*dy)*view.scale];}
function draw(){
  if($('map').hidden)return;
  const canvas=$('map'),ratio=Math.min(2,devicePixelRatio||1),w=canvas.clientWidth,h=canvas.clientHeight;
  if(canvas.width!==Math.round(w*ratio)||canvas.height!==Math.round(h*ratio)){canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);}
  const ctx=canvas.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,w,h);ctx.fillStyle='#e8ebdf';ctx.fillRect(0,0,w,h);
  const g=state?.map;if(!g||!image){view=null;ctx.fillStyle='#78867a';ctx.textAlign='center';ctx.fillText(state?.reason||'지도 수신 대기',w/2,h/2);return;}
  const scale=Math.min((w-32)/g.width,(h-32)/g.height),mw=g.width*scale,mh=g.height*scale;
  view={left:(w-mw)/2,top:(h-mh)/2,h:mh,scale};ctx.drawImage(image,view.left,view.top,mw,mh);
  if(state.path.length){ctx.beginPath();ctx.strokeStyle='#b7853b';ctx.lineWidth=3;state.path.forEach(([x,y],i)=>{const p=worldToCanvas(x,y);i?ctx.lineTo(...p):ctx.moveTo(...p);});ctx.stroke();}
  for(const p of activeMap()?.points||[]){const [x,y]=worldToCanvas(p.x,p.y);ctx.fillStyle=p.id===$('points').value?'#bd7b32':'#315d4d';ctx.beginPath();ctx.arc(x,y,6,0,Math.PI*2);ctx.fill();ctx.font='12px system-ui';ctx.textAlign='left';ctx.fillText(p.name,x+10,y-9);}
  if(selection){const [x,y]=worldToCanvas(selection.x,selection.y);ctx.strokeStyle='#b76542';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(x-9,y);ctx.lineTo(x+9,y);ctx.moveTo(x,y-9);ctx.lineTo(x,y+9);ctx.stroke();}
  if(g.robot){const p=g.robot,[x,y]=worldToCanvas(p.x,p.y);ctx.save();ctx.translate(x,y);ctx.rotate(-(p.yaw-g.yaw));ctx.fillStyle='#c2833d';ctx.beginPath();ctx.moveTo(13,0);ctx.lineTo(-9,-7);ctx.lineTo(-5,0);ctx.lineTo(-9,7);ctx.closePath();ctx.fill();ctx.restore();}
}
$('map').onclick=e=>{
  if(!view||!state.map)return;
  const r=$('map').getBoundingClientRect(),x=(e.clientX-r.left-view.left)/view.scale,y=(view.top+view.h-(e.clientY-r.top))/view.scale,g=state.map;
  if(x<0||y<0||x>g.width||y>g.height)return;
  const c=Math.cos(g.yaw),s=Math.sin(g.yaw);selection={x:g.x+c*x-s*y,y:g.y+s*x+c*y};
  $('selection').textContent=`선택: X ${selection.x.toFixed(2)}m · Y ${selection.y.toFixed(2)}m`;
  buttons();draw();
};
window.addEventListener('resize',draw);
async function poll(){
  try{
    const [a,b]=await Promise.all([fetch('/api/navigation'),fetch('/api/status')]);
    state=await a.json();run=await b.json();render();
    if(state.sensor_reason)notice(state.sensor_reason,true);
    else if($('notice').classList.contains('error')&&!pending)notice(state.message);
  }catch(e){notice('화면 서버 연결이 끊겼습니다.',true);}
  setTimeout(poll,250);
}
try{const session=await(await fetch('/api/session')).json();token=session.token;$('host').value=session.host||'hongik-jetson';notice('R로 수동 조종 켜기 · WASD / 방향키로 이동 · Space/Esc 정지');poll();viewer.connect();}
catch(e){notice('로컬 제어 서버에 연결하지 못했습니다.',true);}
setInterval(()=>{if(owns&&state?.active&&!document.hidden)request('heartbeat',{owner}).catch(()=>{});send();},100);
setInterval(async()=>{try{token=(await(await fetch('/api/session')).json()).token;}catch(e){}},5000);
