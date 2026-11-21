const $=id=>document.getElementById(id),labels={running:'기록 중',completed:'완료',stopped:'중단',interrupted:'프로세스 재시작 · 종료 미확인'};
async function refresh(){
 try{
  const res=await fetch('/api/experiments',{cache:'no-store'});if(!res.ok)throw Error('기록 조회 실패');
  const data=await res.json();$('rows').replaceChildren();
  for(const r of data.experiments){
   let config={},summary={};try{config=JSON.parse(r.config_json||'{}');summary=JSON.parse(r.summary_json||'{}');}catch{}
   const tr=document.createElement('tr');
   function cell(value){const td=document.createElement('td');td.textContent=value;tr.append(td);return td;}
   const date=cell(new Date(r.started_unix*1000).toLocaleString());const id=document.createElement('small');id.textContent=r.run_id;date.append(id);
   cell((config.experiment_case?(config.experiment_label||config.experiment_case)+' · '+Math.round(config.speed*100)+'cm/s · '+(config.repeat||1)+'회차':r.kind)+' / '+r.mode);cell((labels[r.status]||r.status)+(r.reason?' · '+r.reason:''));
   const metrics=summary.experiment_metrics||{},angle=value=>Number.isFinite(value)?value.toFixed(2)+'°':'미확인';cell('R '+angle(metrics.observed_peak_roll_deg)+' / P '+angle(metrics.observed_peak_pitch_deg));cell(String(r.sample_count));
   const links=cell('');for(const fmt of ['csv','json']){const a=document.createElement('a');a.href='/api/experiments/'+encodeURIComponent(r.run_id)+'.'+fmt;a.textContent=fmt.toUpperCase();links.append(a,document.createTextNode('  '));}
   $('rows').append(tr);
  }
  $('status').textContent=data.experiments.length?'최근 '+data.experiments.length+'개 실험 · 3초마다 갱신':'저장된 실험이 없습니다. 실험 시작 버튼을 누르면 기록됩니다.';
 }catch(e){$('status').textContent=e.message;}
}
$('refresh').onclick=refresh;refresh();setInterval(refresh,3000);
