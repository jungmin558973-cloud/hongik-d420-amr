'use strict';
(()=>{
let client=null,seq=0,keys=new Set(),busy=false,enabled=false;
const $t=id=>document.getElementById(id);
async function post(path,data){const session=await fetch('/api/session').then(r=>r.json());const r=await fetch('/api/teleop/'+path,{method:'POST',headers:{'Content-Type':'application/json','X-Lab-Token':session.token},body:JSON.stringify(data)});const d=await r.json();if(!r.ok)throw Error(d.error||'제어 연결 오류');return d;}
function message(s){$t('teleopMessage').textContent=s;}
async function stop(){keys.clear();enabled=false;client=null;try{await post('stop',{});message('정지 · 다시 ARM 필요');}catch(e){message(e.message+' · 물리 STOP 확인');}}
window.enableKeyboardTeleop=async()=>{const c={};document.querySelectorAll('#checks input').forEach(i=>c[i.id]=i.checked);const d=await post('arm',{checks:c,extended_ack:$t('teleopExtended').checked});client=d.client;seq=0;enabled=true;keys.clear();$t('manualSpeed').max=d.speed_limit;$t('manualYaw').max=d.yaw_limit;$t('manualSpeed').value=Math.min(+$t('manualSpeed').value,d.speed_limit);$t('manualYaw').value=Math.min(+$t('manualYaw').value,d.yaw_limit);message('수동 ARM · WASD/방향키를 누르는 동안 이동');};
$t('teleopArm').onclick=async()=>{try{document.querySelectorAll('#checks input').forEach(i=>i.checked=true);await window.enableKeyboardTeleop();}catch(e){message(e.message);}};
$t('teleopStop').onclick=stop;$t('teleopExtended').onchange=()=>{if(enabled)stop();$t('manualSpeed').max=$t('teleopExtended').checked?1.5:.12;$t('manualYaw').max=$t('teleopExtended').checked?5.4:.3;};
const handled=new Set(['KeyW','KeyA','KeyS','KeyD','ArrowUp','ArrowDown','ArrowLeft','ArrowRight']);
window.addEventListener('keydown',e=>{if(e.code==='Space'||e.code==='Escape'){if(enabled){e.preventDefault();stop();}return;}if(!enabled||/INPUT|TEXTAREA|SELECT/.test(e.target.tagName))return;if(handled.has(e.code)){e.preventDefault();keys.add(e.code);}});
window.addEventListener('keyup',e=>{if(handled.has(e.code)){keys.delete(e.code);if(enabled){e.preventDefault();send();}}});
window.addEventListener('blur',()=>{if(enabled)stop();});document.addEventListener('visibilitychange',()=>{if(document.hidden&&enabled)stop();});
async function send(){if(!enabled||busy)return;busy=true;try{const has=(...k)=>k.some(x=>keys.has(x));await post('input',{client,sequence:++seq,forward:Number(has('KeyW','ArrowUp'))-Number(has('KeyS','ArrowDown')),turn:Number(has('KeyA','ArrowLeft'))-Number(has('KeyD','ArrowRight')),speed:+$t('manualSpeed').value,yaw_speed:+$t('manualYaw').value});}catch(e){message(e.message);enabled=false;keys.clear();post('stop',{}).catch(()=>{});}finally{busy=false;}}
setInterval(send,100);
for(const id of ['manualSpeed','manualYaw'])$t(id).oninput=()=>{$t(id+'Value').textContent=$t(id).value;};
})();
