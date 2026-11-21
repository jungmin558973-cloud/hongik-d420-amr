"""Separate physical completion and ten-second capture from analysis quality."""
def assess(status,config,frames,events):
    record_only=config.get('scenario')=='record_only'
    if record_only:return {'success':status=='COMPLETE','folder_status':'기록완료' if status=='COMPLETE' else '기록실패','motion_success':None,'reasons':[]}
    stops=[e for e in events if e.get('event')=='STOP_CONFIRMED' and e.get('segment')=='forward' and 'utc' in e]
    stop=stops[-1] if stops else None
    distance=stop.get('x') if stop else None
    target=config.get('distance',1.5)
    motion=distance is not None and target-.05<=distance<=target+.15
    seconds=max((f.get('utc',0)-stop['utc'] for f in frames),default=0) if stop else 0
    observed=seconds>=9.9
    hard_aborts=[e for e in events if e.get('event')=='ABORT' and not e.get('reason','').startswith('Actual >=1.5s constant-speed interval')]
    reasons=[]
    if not motion:reasons.append('목표 거리 주행 및 실제 정지 확인 부족')
    if not observed:reasons.append('정지 후 10초 영상 부족')
    if hard_aborts or status!='COMPLETE':reasons.append('시험 중단 또는 오류')
    success=motion and observed and not hard_aborts and status=='COMPLETE'
    return {'success':success,'folder_status':'성공' if success else '실패','motion_success':motion,
            'target_distance_m':target,'actual_stop_distance_m':distance,'post_stop_video_s':max(0,seconds),
            'post_stop_video_required_s':10,'reasons':reasons,'analysis_quality_is_separate':True}
