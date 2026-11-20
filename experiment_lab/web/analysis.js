'use strict';
const $=id=>document.getElementById(id);
let runs=[],selected=null,data=null,frameIndex=0,sessionToken='',pendingJob=null,frameTimer=null,frameRequest=0;
const number=(v,d=2)=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(d):'—';
const fileUrl=name=>'/files/'+encodeURIComponent(selected)+'/'+name.split('/').map(encodeURIComponent).join('/');
function error(e){$('toast').textContent=e.message||String(e);$('toast').hidden=false;setTimeout(()=>$('toast').hidden=true,7000);}
async function api(route,body){
 const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Lab-Token':sessionToken},body:JSON.stringify(body)};
 let r=await fetch('/api/'+route,options),d=await r.json();
 if(r.status===403&&body!==undefined&&d.error==='Session token required'){
  sessionToken=(await (await fetch('/api/session')).json()).token;options.headers['X-Lab-Token']=sessionToken;
  r=await fetch('/api/'+route,options);d=await r.json();
 }
 if(!r.ok)throw Error(d.error||'요청 실패');return d;
}
function mode(c){return (c.s_curve?{'OFF':'B','LEVEL':'D','PREDICTIVE':'F'}:{'OFF':'A','LEVEL':'C','PREDICTIVE':'E'})[c.tray]||'?';}
async function refresh(){runs=(await api('runs')).runs.filter(r=>!r.simulated);renderRuns();}
function renderRuns(){
 $('runs').replaceChildren();
 const list=runs.filter(r=>!$('filter').value||Number($('filter').value)===r.config.speed);
 if(!list.length){const p=document.createElement('p');p.className='caption';p.textContent='저장된 실험이 없습니다.';$('runs').append(p);}
 for(const r of list){
  const b=document.createElement('button');b.className='run'+(r.run_id===selected?' selected':'');
  const c=r.config,title=document.createElement('strong'),details=document.createElement('small'),badge=document.createElement('span');
  title.textContent=`${c.speed*100} cm/s · ${c.distance} m · 조건 ${mode(c)}`;
  details.textContent=new Date(r.created*1000).toLocaleString('ko-KR')+` · S ${c.s_curve?(c.s_curve_strength??100)+'/100':'OFF'} / 트레이 ${c.tray}`;
  badge.className='badge'+(r.status==='ABORTED'?' failed':'');badge.textContent=(r.metrics.completion?.folder_status||r.status)+' · '+(r.metrics.analysis_status==='COMPLETE'?'분석 완료':'분석 대기');
  b.append(title,details,badge);b.onclick=()=>select(r.run_id).catch(error);$('runs').append(b);
 }
}
async function select(rid){
 $('analysisVideo').pause();$('analysisVideo').removeAttribute('src');$('analysisVideo').hidden=true;selected=rid;data=null;frameRequest++;$('frame').hidden=true;$('peak').hidden=true;$('frameEmpty').hidden=false;$('peakEmpty').hidden=false;
 $('title').textContent='결과를 불러오는 중…';$('job').textContent='원본 데이터 확인 중';renderRuns();
 const result=await api('review/'+encodeURIComponent(rid));if(selected!==rid)return;data=result;
 history.replaceState(null,'','?run='+encodeURIComponent(rid));
 const c=data.config;$('title').textContent=`${c.speed*100} cm/s · ${c.distance} m · 조건 ${mode(c)}`;
 $('condition').textContent=`S-curve ${c.s_curve?(c.s_curve_strength??100)+'/100':'OFF'} / 트레이 ${c.tray} · S 강도 ${c.s_curve?(c.s_curve_strength??100):0}/100 · 정지 후 ${c.settle+c.post_roll}초 관찰`;
 $('folder').disabled=false;$('analyze').disabled=false;$('analyze').textContent=data.analyzed?'다시 정밀 분석':'OpenCV 정밀 분석';
 $('progress').hidden=true;$('job').textContent=data.analyzed?'분석 결과가 저장되어 있습니다. 그래프를 눌러 검출점을 확인하세요.':'원본 영상·주행 로그가 준비됐습니다. 정밀 분석을 누르면 액면 검출을 시작합니다.';
 const w=data.window,m=data.metrics,peak=m.peak_event||{};
 $('rise').textContent=number(w.max_rise_px);$('rms').textContent=number(w.rms_px);$('settling').textContent=number(m.settling_s);$('distance').textContent=number(m.actual_distance_m,3);
 const warnings=[];if(!data.analyzed)warnings.push('아직 액면 분석 전입니다.');
 if(data.analyzed&&w.valid_frames===0)warnings.push('유효 액면 검출이 없습니다. 컵·파란 액체·마커가 보이는지 확인하세요.');
 if(data.analyzed&&w.valid_frames>0)warnings.push(`제동·정지 구간 유효 프레임 ${w.valid_frames}/${w.total_frames} (${number(w.valid_ratio*100,1)}%).`);
 if(!w.confirmed)warnings.push('제동·정지 로그가 부족해 영상 전체 범위로 표시합니다.');
 if(data.clock.quality!=='good')warnings.push(`시각 동기화 오차 ±${number(data.clock.uncertainty_s*1000,0)}ms: 센서와 영상의 순간 비교에 반영하세요.`);
 if(data.analyzed&&w.valid_frames>0&&m.settling_s==null)warnings.push('관찰 구간 내 안정화 기준 충족은 확인되지 않았습니다.');
 warnings.push('높이 단위 px · mm 길이 보정 전');
 $('quality').textContent=warnings.join(' ');$('quality').className='quality'+(!w.valid_frames||data.clock.quality!=='good'?' warn':'');
 if(peak.available&&data.files.includes('peak_frame.png')){
  $('peak').src=fileUrl('peak_frame.png');$('peak').hidden=false;$('peakEmpty').hidden=true;
  const f=data.series.find(f=>f.seq===peak.frame_seq);$('peakTime').textContent=`최대 상승 ${number(peak.peak_rise_px)} px · 녹화 시작 +${number(f?.t,3)}초 · 프레임 ${peak.frame_seq}`;
  $('peakJump').disabled=false;
 }else{$('peakJump').disabled=true;$('peakTime').textContent='최대 순간은 유효한 점 추적 결과가 있을 때만 표시합니다.';}
 $('evidence').replaceChildren();
 const pairs=[['유효한 액면 프레임',`${w.valid_frames} / ${w.total_frames}`],['시간 동기화',`±${number(data.clock.uncertainty_s*1000,0)} ms`],['검출 기준','ArUco + 파란 액면 점 추적'],['측정 범위',w.confirmed?'제동 0.5초 전 ~ 정지 후 10초':'정지 이벤트 미확인'],['안정화 판정','기준면 ±2px, 연속 1초'],['실제 IMU',data.telemetry.some(r=>r.roll_deg!=null||r.pitch_deg!=null)?'각도 데이터 수신':'미수신 · 보충하지 않음']];
 for(const [label,value] of pairs){const div=document.createElement('div'),a=document.createElement('small'),b=document.createElement('b');a.textContent=label;b.textContent=value;div.append(a,b);$('evidence').append(div);}
 $('downloads').replaceChildren();
 for(const [label,name] of [['최대 순간 PNG','peak_frame.png'],['액면·주행 CSV','synchronized.csv'],['검출점 CSV','peak_surface_points.csv'],['최대 순간 전후 IMU','peak_imu_window.csv'],['검출 표시 영상',data.browser_video||'webcam_annotated.mp4'],['원본 영상',m.video_file||'webcam_raw.avi'],['보고서','report.html'],['실험 설명서','실험설명서.md']]){
  if(!data.files.includes(name))continue;const a=document.createElement('a');a.href=fileUrl(name)+'?download=1';a.textContent=label;$('downloads').append(a);
 }
 $('prepareVideo').disabled=!data.analyzed;$('prepareVideo').hidden=!!data.browser_video;
 if(data.browser_video){$('analysisVideo').src=fileUrl(data.browser_video);$('analysisVideo').hidden=false;$('analysisVideo').playbackRate=Number($('videoRate').value);$('videoInfo').textContent='H.264 검출 표시 영상 · 원본 프레임의 실제 시각 간격으로 재생 · 그래프·최대 순간과 연동';}
 else $('videoInfo').textContent=data.analyzed?'재생용 영상 준비를 한 번 누르면 기존 검출 결과로 재생 파일을 만듭니다.':'OpenCV 정밀 분석을 먼저 실행하세요.';
 $('timeline').max=Math.max(0,data.series.length-1);
 const target=peak.available?data.series.findIndex(f=>f.seq===peak.frame_seq):data.series.findIndex(f=>f.t>=(data.events.find(e=>e.event==='STOP_CONFIRMED')?.t||0));
 setFrame(Math.max(0,target));const job=await api('analysis/'+rid);if(selected===rid&&job.state==='RUNNING')watch(rid);
 else if(selected===rid&&job.state==='ERROR')$('job').textContent='최근 분석을 완료하지 못했습니다: '+job.error;
}
function setFrame(index,fromVideo=false){
 if(!data?.series.length)return;frameIndex=Math.max(0,Math.min(data.series.length-1,Math.round(index)));
 $('timeline').value=frameIndex;const f=data.series[frameIndex];if(!fromVideo&&data.browser_video){$('analysisVideo').pause();$('analysisVideo').currentTime=Math.round(f.t*1000)/1000+.0005;}$('frameTime').textContent=`+${number(f.t,3)} s · #${f.seq}`;
 $('frameCaption').textContent=f.valid?`유효 액면 · 최대 상승 ${number(f.rise)} px · RMS ${number(f.rms)} px`:data.analyzed?'이 프레임은 액면 검출 무효입니다. 측정값을 보간하지 않습니다.':'원본 프레임 · 분석 전';
 drawAll();if(fromVideo===true)return;clearTimeout(frameTimer);const rid=selected,request=++frameRequest;
 frameTimer=setTimeout(()=>{const image=new Image();image.onload=()=>{if(request!==frameRequest||rid!==selected)return;$('frame').src=image.src;$('frame').hidden=false;$('frameEmpty').hidden=true;};image.onerror=()=>{if(request===frameRequest){$('frameEmpty').textContent='프레임을 불러오지 못했습니다.';$('frameEmpty').hidden=false;}};image.src=`/api/review/${encodeURIComponent(rid)}/frame/${frameIndex}.jpg`;},70);
}
function draw(id,lines,title){
 const canvas=$(id),rect=canvas.getBoundingClientRect(),ratio=devicePixelRatio||1;canvas.width=Math.max(1,rect.width*ratio);canvas.height=rect.height*ratio;
 const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);const W=rect.width,H=rect.height,L=49,R=17,T=27,B=26,w=W-L-R,h=H-T-B;
 if(!data?.series.length)return;
 const duration=Math.max(.01,data.series.at(-1).t),values=lines.flatMap(l=>l.rows.map(r=>r[l.key])).filter(v=>typeof v==='number'&&Number.isFinite(v));
 let lo=Math.min(0,...values),hi=Math.max(id==='speed'?.05:1,...values);const margin=(hi-lo)*.12;lo-=margin;hi+=margin;
 const x=t=>L+t/duration*w,y=v=>T+(hi-v)/(hi-lo)*h;
 ctx.font='11px Segoe UI';ctx.fillStyle='#66756d';ctx.fillText(title,L,13);
 for(let i=0;i<5;i++){const v=lo+(hi-lo)*i/4;ctx.strokeStyle='#e5e6da';ctx.beginPath();ctx.moveTo(L,y(v));ctx.lineTo(W-R,y(v));ctx.stroke();ctx.fillText(v.toFixed(id==='speed'?2:1),3,y(v)+4);}
 for(let i=0;i<=5;i++){const t=duration*i/5;ctx.fillText(t.toFixed(1)+'s',x(t)-10,H-5);}
 for(const e of data.events.filter(e=>['BRAKE_REQUEST','STOP_CONFIRMED'].includes(e.event))){ctx.strokeStyle=e.event==='BRAKE_REQUEST'?'#bb6c40':'#819b78';ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(x(e.t),T);ctx.lineTo(x(e.t),H-B);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=ctx.strokeStyle;ctx.fillText(e.event==='BRAKE_REQUEST'?'제동':'정지',x(e.t)+3,T+11);}
 for(const l of lines){ctx.strokeStyle=l.color;ctx.lineWidth=1.8;ctx.beginPath();let last=null;for(const r of l.rows){const v=r[l.key];if(typeof v!=='number'||!Number.isFinite(v)){last=null;continue;}if(last===null||r.t-last>.2)ctx.moveTo(x(r.t),y(v));else ctx.lineTo(x(r.t),y(v));last=r.t;}ctx.stroke();}
 ctx.strokeStyle='#263a33';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x(data.series[frameIndex].t),T);ctx.lineTo(x(data.series[frameIndex].t),H-B);ctx.stroke();
 canvas.onclick=e=>{const t=Math.max(0,Math.min(duration,(e.clientX-rect.left-L)/w*duration));let best=0;for(let i=1;i<data.series.length;i++)if(Math.abs(data.series[i].t-t)<Math.abs(data.series[best].t-t))best=i;setFrame(best);};
}
function drawAll(){draw('slosh',[{rows:data?.series||[],key:'rise',color:'#164c3d'},{rows:data?.series||[],key:'rms',color:'#d28d51'}],'액면 상승 / RMS (px)');draw('speed',[{rows:data?.telemetry||[],key:'v',color:'#164c3d'},{rows:data?.telemetry||[],key:'requested_v',color:'#c99f70'}],'실제 속도 / 목표 속도 (m/s)');}
async function watch(rid){
 pendingJob=rid;$('analyze').disabled=true;
 try{while(pendingJob===rid){const j=await api('analysis/'+rid);if(selected===rid){$('progress').hidden=false;$('progress').value=j.percent||0;$('job').textContent=`${j.stage||'분석 준비'} · ${number(j.percent||0,0)}%${j.total?' · '+(j.done||0)+' / '+j.total+' 프레임':''}`;}
  if(j.state==='ERROR')throw Error(j.error);if(j.state==='COMPLETE'){pendingJob=null;await refresh();if(selected===rid)await select(rid);return;}
  if(j.state!=='RUNNING')break;await new Promise(r=>setTimeout(r,900));}
 }catch(e){if(selected===rid){$('job').textContent='분석을 완료하지 못했습니다: '+e.message;$('progress').hidden=true;}error(e);}
 finally{if(pendingJob===rid)pendingJob=null;if(selected===rid)$('analyze').disabled=false;}
}
$('analyze').onclick=async()=>{if(!selected)return;const rid=selected;$('analyze').disabled=true;try{await api('analyze/'+rid,{});await watch(rid);}catch(e){error(e);$('job').textContent=e.message;}finally{$('analyze').disabled=false;}};
$('prepareVideo').onclick=async()=>{if(!selected)return;const rid=selected;$('prepareVideo').disabled=true;try{await api('analyze/'+rid,{browser_only:true});await watch(rid);}catch(e){error(e);$('videoInfo').textContent=e.message;}finally{$('prepareVideo').disabled=false;}};
 function videoFrame(){if(!data?.series.length)return;const t=$('analysisVideo').currentTime;let lo=0,hi=data.series.length-1;while(lo<hi){const mid=Math.ceil((lo+hi)/2);if(data.series[mid].t<=t+.001)lo=mid;else hi=mid-1;}setFrame(lo,true);}
 $('analysisVideo').onplay=()=>{$('frame').hidden=true;$('frameEmpty').hidden=false;$('frameEmpty').textContent='검출 표시 영상 재생 중 · 일시정지하면 해당 프레임을 확인할 수 있습니다.';};$('analysisVideo').ontimeupdate=videoFrame;$('analysisVideo').onpause=()=>{if(data?.browser_video){videoFrame();setFrame(frameIndex,'paused');}};
 $('analysisVideo').onerror=()=>{if(data?.browser_video)$('videoInfo').textContent='브라우저 재생 오류: Chrome/Edge에서 열거나 검출 표시 영상을 내려받으세요.';};
 $('videoRate').onchange=()=>$('analysisVideo').playbackRate=Number($('videoRate').value);
 $('folder').onclick=()=>api('folder/'+selected,{}).catch(error);
$('refresh').onclick=()=>refresh().catch(error);$('filter').onchange=renderRuns;
$('timeline').oninput=()=>setFrame(Number($('timeline').value));$('prev').onclick=()=>setFrame(frameIndex-1);$('next').onclick=()=>setFrame(frameIndex+1);
$('peakJump').onclick=()=>{const i=data.series.findIndex(f=>f.seq===data.metrics.peak_event?.frame_seq);if(i>=0)setFrame(i);};
window.addEventListener('resize',drawAll);
(async()=>{sessionToken=(await api('session')).token;await refresh();const rid=new URLSearchParams(location.search).get('run')||runs.find(r=>r.metrics.completion?.success)?.run_id||runs[0]?.run_id;if(rid)await select(rid);})().catch(error);
