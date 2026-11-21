'use strict';
// Reuse the existing validated config/API. No second motion/control path.
const quickCard=document.createElement('section');quickCard.id='quickCard';
quickCard.innerHTML='<h2>1.6m 주행 · 급정지 촬영</h2><p>시작 → 1.6m 주행 → 급정지 → 정지 후 10초 녹화 → 자동 저장·종료</p><div id="quickSpeed"></div><div id="quickModes"></div><p id="quickNext" class="muted"></p><button id="quickStart" class="primary">▶ 실험 시작 · 녹화</button><p id="quickStatus" role="status">연결 확인 중</p><p class="muted">장애물 자동정지 OFF · 시작 클릭 = 통로·컵·비상정지 확인 및 주행 승인 · 1.6m 거리 정지</p>';
const main=document.querySelector('main');main.prepend(quickCard);
const folders=document.createElement('div');folders.className='actions';folders.innerHTML='<button id="quickFolder">📁 저장 폴더 열기</button><button id="quickRunFolder">최근 실험 폴더</button>';$('quickStart').after(folders);
bind('quickFolder',()=>api('folder',{}));
bind('quickRunFolder',()=>{const rid=lastState.run_id||runs.find(r=>!r.simulated)?.run_id;if(!rid)throw Error('저장된 촬영이 없습니다.');return api('folder/'+rid,{})});
const oldLayout=document.querySelector('.layout');const advanced=document.createElement('details');advanced.id='advancedSettings';advanced.innerHTML='<summary>상세 설정 · 수동 조종 · 연결 진단</summary>';oldLayout.before(advanced);advanced.append(oldLayout);
// Keep one copy of every control so existing handlers and safety guards apply.
const speedLabel=$('speed').closest('label');$('quickSpeed').append(speedLabel);speedLabel.firstChild.textContent='목표 속도';
for(const option of $('speed').options){const value=option.value;option.value=value;option.textContent=String(Math.round(Number(value)*100))+' cm/s';}
$('quickModes').append($('modes'));document.querySelector('#modes button').click();
const previewSection=$('preview').closest('section');const quickPreview=document.createElement('section');quickPreview.id='quickPreview';quickPreview.innerHTML='<div id="quickCameraHeading" class="heading"></div>';
quickCard.after(quickPreview);$('quickCameraHeading').append($('cameraName'),$('fps'));quickPreview.append($('preview').closest('.camera'));
const results=$('runs').closest('section');quickPreview.after(results);results.before($('peakPanel'));
const dialog=document.createElement('dialog');dialog.id='quickSafety';dialog.innerHTML='<h2>현장 안전 확인</h2><p>확인 후 시작하면 로봇이 자동으로 움직입니다.</p><div id="quickChecks"></div><div class="actions"><button id="quickCancel">취소</button><button id="quickConfirm" class="primary">확인 완료 · 시작</button></div>';
document.body.append(dialog);$('quickChecks').append($('checks'));
const firstConsent=document.createElement('label');firstConsent.id='firstTrialConsent';firstConsent.innerHTML='<input id="firstTrialAck" type="checkbox">트레이 OFF · 20cm/s · 1.6m 첫 실차 검증 촬영을 직접 승인합니다. 검증 완료로 등록되지 않으며 IMU 미수신은 별도 표시됩니다.';$('quickChecks').append(firstConsent);
const manualSection=$('teleopArm').closest('section');const manualDetails=document.createElement('details');manualDetails.id='quickManual';manualDetails.innerHTML='<summary>⌨ WASD 키보드 조종</summary>';quickPreview.before(manualDetails);manualDetails.append(manualSection);$('teleopArm').textContent='현장 확인 후 키보드 조종 켜기';
const strengthControl=document.createElement('div');strengthControl.id='curveControl';strengthControl.innerHTML='<label>S-curve 강도 <input id="curveStrengthNumber" type="number" min="0" max="100" step="1" value="0"> / 100</label><input id="curveStrength" type="range" min="0" max="100" step="1" value="0"><p id="curveDescription" class="muted"></p>';$('quickModes').after(strengthControl);
function updateStrengthDescription(){const v=Number($('curveStrength').value);$('curveStrengthNumber').value=v;$('curveDescription').textContent=v===0?'0 = 기존 OFF 가속 · 1.6m에서 급정지 유지':`${v} / 100 · 적용 Jerk ${(Number($('jerk').value)*100/v).toFixed(3)} m/s³ · 100이 가장 완만함 · 급정지는 우회`;} 
function setStrength(v){v=Math.round(Math.max(0,Math.min(100,Number(v)||0)));$('curveStrength').value=v;const tray=modes[selectedMode][1];const key=Object.keys(modes).find(k=>modes[k][0]===(v>0)&&modes[k][1]===tray);const b=[...document.querySelectorAll('#modes button')].find(b=>b.querySelector('b').textContent===key);b.click();updateStrengthDescription();}
for(const b of document.querySelectorAll('#modes button')){const original=b.onclick;b.onclick=()=>{original();if(!modes[selectedMode][0])$('curveStrength').value=0;else if(Number($('curveStrength').value)===0)$('curveStrength').value=100;updateStrengthDescription();};}
$('curveStrength').oninput=()=>setStrength($('curveStrength').value);$('curveStrengthNumber').onchange=()=>setStrength($('curveStrengthNumber').value);$('jerk').addEventListener('input',updateStrengthDescription);updateStrengthDescription();
let quickBusy=false,pendingQuickConfig=null,quickSafetyAction='experiment';
window.openManualSafety=()=>{quickSafetyAction='manual';firstConsent.hidden=true;Object.keys(checkLabels).forEach(k=>$(k).checked=false);$('quickConfirm').textContent='확인 완료 · 키보드 조종 켜기';dialog.showModal();};
function quickConfig(){const c=config();c.distance=1.6;c.return_home=false;c.repeats=3;c.scenario='planned_stop';c.record_priority=true;c.require_4k=false;c.stop_semantics='brake_at_distance';c.obstacle_stop=false;c.settle=8;c.post_roll=2;return c;}
bind('quickStart',async()=>{
 if(quickBusy)return;quickBusy=true;$('quickStart').disabled=true;
 try{
  const candidate=quickConfig();
  candidate.commissioning=candidate.tray==='OFF';
  $('quickStatus').textContent='연결 확인 · 녹화 준비 중…';
  const result=await api('preflight',candidate);
  if(!result.ready)throw Error(result.blockers.join(' / '));
  // The start button itself is the operator's explicit current safety approval.
  const approved=Object.fromEntries(Object.keys(checkLabels).map(k=>[k,true]));
  await api('teleop/stop',{});await api('arm',approved);$('quickStatus').textContent='영상·주행 로그 시작 중…';
  await api('start',candidate);$('quickStatus').textContent='녹화 중 · 1.6m 자동 주행';
 }finally{quickBusy=false;$('quickStart').disabled=false;}
});
$('quickCancel').onclick=()=>dialog.close();
bind('quickConfirm',async()=>{
 if(quickBusy)return;quickBusy=true;$('quickConfirm').disabled=true;
 try{if(quickSafetyAction==='manual'){await window.enableKeyboardTeleop();dialog.close();return;}await api('arm',checks());await api('start',pendingQuickConfig);dialog.close();$('quickStatus').textContent='실험 중 · 녹화와 로그를 저장하고 있습니다.';}
 finally{quickBusy=false;$('quickConfirm').disabled=false;}
});
setInterval(()=>{
 const s=lastState;if(!s.camera)return;
 const current=quickConfig();const attempts=runs.filter(r=>!r.simulated&&r.config.scenario==='planned_stop'&&r.config.distance===1.6&&r.config.speed===current.speed&&r.config.s_curve===current.s_curve&&r.config.tray===current.tray&&new Date(r.created*1000).toDateString()===new Date().toDateString());
 $('quickNext').textContent=`설정별 저장: 거리 ${current.distance.toFixed(2)}m · 속도 ${current.speed*100}cm/s · S ${current.s_curve?current.s_curve_strength+' / 100':'OFF'} · 트레이 ${current.tray} · 관찰 10초 · 촬영 시각과 성공 여부 표시`;
 $('quickStart').disabled=quickBusy||s.starting||s.active||s.saving||s.analysis_running;
 $('curveStrength').disabled=$('curveStrengthNumber').disabled=!!(s.active||s.starting||s.saving||s.analysis_running);
 if(quickBusy||s.starting)$('quickStatus').textContent=(s.start_step||'실험 시작 준비')+' · 잠시 기다려 주세요';
 else if(s.active)$('quickStatus').textContent=['SETTLING','POSTROLL'].includes(s.state)?'정지 후 녹화 중 · '+(s.post_stop_remaining_s==null?'10':s.post_stop_remaining_s.toFixed(1))+'초 남음':'1.6m 자동 주행 · 녹화·로그 저장 중';
 else if(s.saving)$('quickStatus').textContent='영상·데이터 저장 중';
 else if(s.analysis_running)$('quickStatus').textContent='사후 정밀 분석 중';
 else if(!s.camera.ready)$('quickStatus').textContent='C920 USB 연결 대기 · 연결하면 자동 재시도'+(!s.can_arm?' / Jetson 연결도 대기 중':'');
 else if(!s.can_arm)$('quickStatus').textContent='영상 준비 완료 · Jetson 자동 주행 연결 확인 필요';
 else if(s.state==='COMPLETE')$('quickStatus').textContent=(s.metrics.completion?.success?'성공':'촬영 종료')+' · 실험 폴더에 영상·CSV·설명서·성공 결과 저장 완료';
 else $('quickStatus').textContent='연결 준비 완료 · 시작을 누르고 현장 안전 확인 후 촬영하세요.';
},500);

window.addEventListener("keydown",e=>{if((e.code==="Space"||e.code==="Escape")&&lastState.active){e.preventDefault();api("stop",{}).catch(x=>toast(x.message));}});
