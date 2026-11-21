"""Named experimental conditions, shared by API and Jetson control owner."""
SPEEDS=(.20,.30,.40)
CASES={
 'baseline':dict(label='무보정 · PWM 고정',tray='OFF',smooth=False),
 'level':dict(label='수평 유지',tray='LEVEL',smooth=False),
 'active':dict(label='능동 트레이 + 수평 유지',tray='PREDICTIVE',smooth=False),
 'scurve':dict(label='S-curve 감속 · 트레이 무보정',tray='OFF',smooth=True),
 'combined':dict(label='능동 트레이 + S-curve',tray='PREDICTIVE',smooth=True),
}
def parse_case(case,speed):
 if not isinstance(case,str) or case not in CASES:raise ValueError('실험 조건을 확인하세요.')
 if type(speed) not in (int,float) or not any(abs(speed-v)<1e-8 for v in SPEEDS):raise ValueError('실험 속도는 20/30/40cm/s 중에서 선택하세요.')
 return {'id':case,**CASES[case],'speed_m_s':speed}
