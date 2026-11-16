let box,title,reason,detail,dismissed='',currentKey='';
const phases={depth_first:'첫 직선',depth_brake:'중간 급정지',depth_turn_settle:'회전 전 정지 확인',depth_turn:'오른쪽 90° 회전',depth_second:'두 번째 직선',depth_finish:'도착 정지 확인'};
function initialize(){
 if(box)return;
 const style=document.createElement('style');style.textContent='.stop-alert{position:sticky;top:0;z-index:2000;margin:0;padding:22px 28px;border:4px solid #b24132;border-radius:0;background:#fff0e8;color:#742719;box-shadow:0 5px 20px #0003;display:grid;grid-template-columns:1fr auto;gap:8px 20px}.stop-alert[hidden]{display:none}.stop-alert strong{font:700 clamp(24px,3vw,38px) system-ui}.stop-alert p{margin:0;font:600 clamp(18px,2vw,25px)/1.5 system-ui;white-space:pre-line;grid-column:1}.stop-alert small{font:14px/1.6 system-ui;grid-column:1}.stop-alert button{grid-column:2;grid-row:1/4;align-self:start;background:white;color:#742719;border:1px solid;padding:10px 18px;cursor:pointer}.stop-alert.complete{background:#e6f3e7;border-color:#35674b;color:#254b38}.stop-alert.pause{background:#fff4d4;border-color:#b27d24;color:#644716}';document.head.append(style);
 box=document.createElement('section');box.className='stop-alert';box.id='stop-alert';box.hidden=true;box.setAttribute('role','alert');box.setAttribute('aria-live','assertive');
 title=document.createElement('strong');reason=document.createElement('p');detail=document.createElement('small');const close=document.createElement('button');close.textContent='확인 · 안내 닫기';close.onclick=()=>{dismissed=currentKey;box.hidden=true;};
 box.append(title,reason,detail,close);document.body.prepend(box);
}
export function updateStopNotice(run){
 initialize();if(!run)return;
 let heading='',type='';
 if(run.active&&run.depth_paused){heading='주행 일시 정지';type='pause';}
 else if(run.active&&run.state==='depth_brake'){heading='중간 급정지 시험 중';type='pause';}
 else if(!run.active&&run.state==='stopped')heading='주행 중단 · 정지 사유';
 else if(!run.active&&run.state==='done'){heading='주행 완료';type='complete';}
 else{box.hidden=true;if(run.active)dismissed='';return;}
 currentKey=[run.route_id||run.recording?.run_id||run.kind,run.state,run.message].join('|');
 if(currentKey===dismissed){box.hidden=true;return;}
 box.className='stop-alert '+type;title.textContent=heading;reason.textContent=run.message||'상태 확인 필요';
 const phase=run.stop_context?.phase||run.state;
 detail.textContent=(phases[phase]?'발생 단계: '+phases[phase]+' · ':'')+(run.route_id?'실험 ID: '+run.route_id+' · ':'')+(type==='complete'?'실험 기록에서 데이터를 내려받으세요.':run.active?'예정된 정지 또는 센서 복구 대기입니다.':'자동 재출발하지 않습니다. 실험 기록에서 세부 원인을 확인하세요.');box.hidden=false;
}
