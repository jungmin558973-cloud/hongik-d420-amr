import math
def parse_options(data):
 if data is None:return None
 defaults=dict(test='brake_line',smooth_pct=0.,turn_rate=.6,deceleration=.5,latency=.1,line_m=1.6,tray='CHASSIS',reuse_calibration=True)
 if not isinstance(data,dict) or set(data)-set(defaults):raise ValueError('실험 세팅 항목 오류')
 c={**defaults,**data}
 if c['test'] not in ('brake_line','l_route') or c['tray'] not in ('CHASSIS','LEVEL','PREDICTIVE'):raise ValueError('시험/트레이 종류 오류')
 if type(c['reuse_calibration']) is not bool:raise ValueError('제동 보정 재사용 설정 오류')
 for k,lo,hi in [('smooth_pct',0,100),('turn_rate',.2,.8),('deceleration',.1,3.),('latency',0,.3),('line_m',1.6,1.6)]:
  if type(c[k]) not in (int,float) or not math.isfinite(c[k]) or not lo<=c[k]<=hi:raise ValueError(k+' 설정 범위 오류')
 return c
