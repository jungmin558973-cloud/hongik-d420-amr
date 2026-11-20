import {updateStopNotice} from './stop_notice.js';
const $=id=>document.getElementById(id),owner=crypto.randomUUID();let token='',pending=false,run=null;
$('strength').oninput=()=>{$('strength-label').textContent=$('strength').value+'%';};
async function request(action,body){const res=await fetch('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Hongik-Control':token},body:JSON.stringify(body)});const data=await res.json();if(!data.ok)throw Error(data.error||'요청 실패');return data;}
function buttons(){for(const id of ['brake','route'])$(id).disabled=pending||!!run?.active||!run?.depth_route?.ready;}
async function start(test){if(pending||run?.active)return;pending=true;buttons();try{
 const options={test,smooth_pct:Number($('strength').value),turn_rate:Number($('turn').value),deceleration:Number($('deceleration').value),latency:.1,line_m:1.6,tray:$('tray').value,reuse_calibration:$('reuse').checked};
 const data=await request('depth-route-start',{mode:'real',owner,trial:false,brake:true,first:3.2,second:2.2,turn_extension:.3,config:{speed:Number($('speed').value)/100},experiment_options:options,tray_level:false,tray_predictive:false});
 run=data.state;$('status').textContent=run.message;
 }catch(e){$('status').textContent=e.message;}finally{pending=false;buttons();}}
$('brake').onclick=()=>start('brake_line');$('route').onclick=()=>start('l_route');$('stop').onclick=()=>request('stop',{}).catch(e=>{$('status').textContent=e.message;});
window.addEventListener('keydown',e=>{if(e.code==='Escape'||e.code==='Space'&&!['INPUT','SELECT'].includes(document.activeElement?.tagName)){e.preventDefault();$('stop').click();}});
async function poll(){try{run=await(await fetch('/api/status',{cache:'no-store'})).json();updateStopNotice(run);if(!pending)$('status').textContent=run.message||'대기';$('result').textContent=Number.isFinite(run.line_error_m)?'실제 정지 위치 오차: '+(run.line_error_m*100).toFixed(1)+'cm (+: 선 초과 / −: 선 앞) · 실험 기록에서 전체 신호 확인':'';buttons();}catch{$('status').textContent='화면 연결 단절 · Jetson 주행은 계속될 수 있습니다.';}setTimeout(poll,300);}
try{token=(await(await fetch('/api/session')).json()).token;poll();}catch{$('status').textContent='앱 연결을 확인하세요.';}
