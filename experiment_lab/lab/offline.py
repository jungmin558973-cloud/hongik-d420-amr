"""Analyze preserved original video using original frame timestamps after capture."""
import json
from pathlib import Path
import cv2
from .vision import Tracker
from .storage import Store,read_jsonl,dump


def analyze(store,rid,progress=None):
    progress=progress or (lambda **kwargs:None)
    progress(stage='정지 기준면 확인',percent=0)
    path=store.path(rid)
    manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
    if manifest['status']=='RECORDING':raise ValueError('Finish recording before analysis')
    settings=json.loads((path/'roi.json').read_text(encoding='utf-8'))
    reference=path/'stationary_reference.json'
    if reference.exists():settings.update(json.loads(reference.read_text(encoding='utf-8')))
    if settings.get('method')=='marker_blue':
        # The weak blue tint on wet/transparent cup walls is not the filled
        # liquid. Keep hue bounds, require saturated blue and reject dark tray.
        low=list(settings.get('hsv_lower',[90,80,40]))
        low[1]=max(low[1],150);low[2]=max(low[2],60)
        if settings.get('hsv_lower')!=low:
            settings.pop('reference_profile',None);settings.pop('reference_width',None)
        settings['hsv_lower']=low
    dump(path/'analysis_settings.json',{'settings':settings,'algorithm':'marker_blue_saturated_v1','reason':'Reject weak blue cup-wall tint and dark tray; original roi.json is preserved'})
    rows=read_jsonl(path/'camera_frames.jsonl');video=source_video(path)
    if settings.get('method')=='marker_blue' and not settings.get('reference_profile'):
        settings=tail_reference(path,settings,rows,video,manifest.get('clock',{}))
    tracker=Tracker(settings);capture=cv2.VideoCapture(str(video));writer=None
    progress(stage='액면 점 추적',percent=5,total=len(rows),done=0)
    temp=path/'camera_analysis.jsonl.tmp';count=0
    try:
        if not capture.isOpened():raise ValueError('Original video cannot be opened')
        with temp.open('w',encoding='utf-8') as f:
            for metadata in rows:
                ok,frame=capture.read()
                if not ok:raise ValueError('Video/frame timestamp count mismatch; do not shift sensor synchronization')
                measurement,overlay=tracker.process(frame,metadata.get('source_video_s') or (metadata['utc']-rows[0]['utc']))
                if writer is None:
                    fps=capture.get(cv2.CAP_PROP_FPS) or 30.
                    writer=cv2.VideoWriter(str(path/'webcam_annotated.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),fps,(frame.shape[1],frame.shape[0]))
                    if not writer.isOpened():raise ValueError('Annotated output cannot open')
                writer.write(overlay);f.write(json.dumps({**metadata,**measurement,'analysis_deferred':False},allow_nan=False)+'\n');count+=1
                if count%15==0:progress(stage='액면 점 추적',percent=5+85*count/max(1,len(rows)),done=count,total=len(rows))
            ok,_=capture.read()
            if ok:raise ValueError('Extra video frames without timestamps')
        if count==0:raise ValueError('No captured timestamps')
    finally:
        capture.release()
        if writer is not None:writer.release()
    temp.replace(path/'camera_analysis.jsonl')
    dump(path/'offline_analysis.json',{'frames':count,'source':video.name,'original_timestamps_preserved':True})
    from .browser_video import make_browser_video
    make_browser_video(path,progress)
    progress(stage='최대 순간·그래프·보고서 저장',percent=98,done=count,total=len(rows))
    result=store.finalize(rid,manifest['status'],manifest['clock'])
    with store.connect() as db:db.execute("UPDATE upload_queue SET state='PENDING',next_at=0 WHERE run_id=?",(rid,))
    return result


def source_video(path):
    return path/('webcam_raw.avi' if (path/'webcam_raw.avi').exists() else 'webcam_raw.mp4')


def tail_reference(path,settings,rows,video,clock):
    """Use only a confirmed stopped tail, never first appearance during braking."""
    events=read_jsonl(path/'events.jsonl');offset=clock.get('jetson_minus_windows_s',0)
    stops=[e['utc']-(offset if e.get('source')=='jetson' else 0) for e in events if e.get('event')=='STOP_CONFIRMED' and e.get('segment')=='forward' and 'utc' in e]
    if not rows or not stops:raise ValueError('정지 기준면이 없습니다. 정지 확인 로그와 안정된 마지막 영상이 필요합니다.')
    start=max(rows[-1]['utc']-3,max(stops)+2)
    chosen=[(i,r) for i,r in enumerate(rows) if r['utc']>=start]
    if len(chosen)<30 or chosen[-1][1]['utc']-chosen[0][1]['utc']<2:raise ValueError('정지 후 기준면을 만들 2초·30프레임 이상의 영상이 부족합니다.')
    robot=read_jsonl(path/'robot_telemetry.jsonl')
    observed=[r for r in robot if chosen[0][1]['utc']<=r.get('utc',0)-offset<=chosen[-1][1]['utc']]
    if len(observed)<10 or any(r.get('v') is None or abs(r['v'])>.008 for r in observed):raise ValueError('기준 영상 구간의 실제 정지 오도메트리를 확인할 수 없습니다.')
    from .marker_blue import MarkerBlue
    detector=MarkerBlue(dict(settings));detector.begin_reference(chosen[0][1]['utc'])
    cap=cv2.VideoCapture(str(video));cap.set(cv2.CAP_PROP_POS_FRAMES,chosen[0][0])
    try:
        for i,r in chosen:
            ok,frame=cap.read()
            if not ok:raise ValueError('정지 기준 영상 프레임 누락')
            detector.process(frame,timestamp=r['utc'])
    finally:cap.release()
    if detector.reference is None:raise ValueError('마지막 액면이 불안정하거나 보이지 않아 기준면 저장을 거절했습니다.')
    dump(path/'stationary_reference.json',detector.settings)
    dump(path/'reference_source.json',{'source':'confirmed stationary tail','start_utc':chosen[0][1]['utc'],'end_utc':chosen[-1][1]['utc'],'odom_samples':len(observed),'clock_quality':clock.get('quality')})
    return detector.settings


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--root',default=str(Path(__file__).resolve().parents[1]/'experiments'));p.add_argument('--run-id',required=True)
    a=p.parse_args();result=analyze(Store(a.root),a.run_id);print(json.dumps(result,ensure_ascii=False,indent=2))
