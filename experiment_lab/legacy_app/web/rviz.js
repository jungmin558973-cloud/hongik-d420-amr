import RFB from './vendor/novnc/core/rfb.js';

export function rvizViewer({getToken,request,onFocus}){
  const screen=document.getElementById('rviz-screen'),status=document.getElementById('rviz-status');
  let client=null,interactive=false,locked=false,closed=false,retry=null;
  function input(){
    if(client){client.viewOnly=locked||!interactive;client.focusOnClick=interactive&&!locked;}
    screen.classList.toggle('view-only',locked||!interactive);
    document.getElementById('rviz-interact').textContent=interactive&&!locked?'RViz 조작 종료':'RViz 확대·이동';
  }
  async function connect(){
    if(closed)return;
    status.textContent='RViz 연결 중';
    try{
      await request('rviz/start');
      if(closed)return;
      client=new RFB(screen,`ws://${location.host}/api/rviz/socket?token=${encodeURIComponent(getToken())}`,{shared:true});
      client.scaleViewport=true;client.resizeSession=false;input();
      client.addEventListener('connect',()=>{status.textContent='실제 RViz 연결됨';input();});
      client.addEventListener('disconnect',()=>{
        status.textContent='RViz 재연결 중';client=null;
        if(!closed)retry=setTimeout(connect,3000);
      });
    }catch(e){status.textContent='RViz 준비 중 · '+e.message;if(!closed)retry=setTimeout(connect,5000);}
  }
  screen.addEventListener('pointerdown',()=>{if(locked||!interactive)onFocus();});
  window.addEventListener('pagehide',()=>{closed=true;clearTimeout(retry);client?.disconnect();});
  return {connect,setLocked(value){locked=value;if(value)interactive=false;input();},
    toggle(){if(locked)return;interactive=!interactive;input();if(!interactive)onFocus();}};
}
