export async function experimentMatrix(start){
 const root=document.getElementById('experiment-matrix');
 try{
  const res=await fetch('/api/experiment-cases');if(!res.ok)throw Error('조건 목록 조회 실패');const plan=await res.json();
  const table=document.createElement('table');table.className='experiment-matrix';const header=document.createElement('tr');
  for(const value of ['비교 조건',...plan.speeds_cm_s.map(s=>s+'cm/s')]){const th=document.createElement('th');th.textContent=value;header.append(th);}table.append(header);
  for(const condition of plan.cases){const tr=document.createElement('tr'),label=document.createElement('th');label.textContent=condition.label;tr.append(label);
   for(const speed of plan.speeds_cm_s){const td=document.createElement('td'),b=document.createElement('button');b.textContent='▶ '+speed;b.dataset.case=condition.id;b.dataset.speed=speed;b.disabled=true;b.onclick=()=>start(condition,speed);td.append(b);tr.append(td);}table.append(tr);
  }
  root.replaceChildren(table);
 }catch(e){root.textContent='실험 조건을 불러오지 못했습니다: '+e.message;}
}
export function matrixState(disabled){for(const b of document.querySelectorAll('#experiment-matrix button'))b.disabled=disabled;}
