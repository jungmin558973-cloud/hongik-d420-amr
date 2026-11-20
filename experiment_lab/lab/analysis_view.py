"""Read-only, timestamp-based review of preserved experiments; no motor APIs."""
import json
import math
from functools import lru_cache
from pathlib import Path
from flask import jsonify,Response,send_from_directory
from .storage import read_jsonl


@lru_cache(maxsize=4)
def cached_rows(filename,mtime,size):
    return read_jsonl(filename)


def rows(path):
    if not path.exists():return []
    st=path.stat()
    return cached_rows(str(path),st.st_mtime_ns,st.st_size)


def review(path):
    manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
    if manifest['status'] in ('RECORDING','SAVING'):raise ValueError('촬영과 저장이 끝난 실험을 선택하세요.')
    frames=rows(path/'camera_analysis.jsonl') or rows(path/'camera_frames.jsonl')
    clock=manifest.get('clock',{});offset=clock.get('jetson_minus_windows_s',0)
    origin=frames[0]['utc'] if frames else manifest.get('closed_utc',0)
    events=[{**e,'t':e['utc']-(offset if e.get('source')=='jetson' else 0)-origin} for e in rows(path/'events.jsonl') if 'utc' in e]
    brakes=[e['t'] for e in events if e.get('event')=='BRAKE_REQUEST' and e.get('segment')=='forward']
    stops=[e['t'] for e in events if e.get('event')=='STOP_CONFIRMED' and e.get('segment')=='forward']
    start=brakes[0]-.5 if brakes else 0;end=stops[0]+10 if stops else float('inf')
    series=[];point_values=[];window_total=window_valid=0
    for i,f in enumerate(frames):
        values=[x for x in f.get('profile_displacement_px',[]) if isinstance(x,(int,float)) and math.isfinite(x)] if f.get('valid') else []
        t=f['utc']-origin;in_window=start<=t<=end
        if in_window:
            window_total+=1
            if values:window_valid+=1;point_values.extend(values)
        series.append({'index':i,'seq':f.get('seq',i),'t':t,'valid':bool(values),
                       'rise':max(values) if values else None,'fall':min(values) if values else None,
                       'rms':math.sqrt(sum(v*v for v in values)/len(values)) if values else None})
    telemetry=[{'t':r['utc']-offset-origin,**{k:r.get(k) for k in ['v','requested_v','roll_deg','pitch_deg']}} for r in rows(path/'robot_telemetry.jsonl')]
    metrics=json.loads((path/'metrics.json').read_text(encoding='utf-8')) if (path/'metrics.json').exists() else {}
    files=[p.relative_to(path).as_posix() for p in path.rglob('*') if p.is_file() and p.suffix in ('.csv','.png','.mp4','.avi','.html','.md')]
    return {'config':json.loads((path/'config.json').read_text(encoding='utf-8')),'metrics':metrics,'clock':clock,
            'browser_video':'webcam_analysis_browser.mp4' if (path/'webcam_analysis_browser.mp4').exists() else None,
            'series':series,'telemetry':telemetry,'events':events,'files':files,'analyzed':(path/'camera_analysis.jsonl').exists(),
            'window':{'start':start,'end':min(end,series[-1]['t']) if series else 0,'confirmed':bool(brakes and stops),
                      'max_rise_px':max(point_values) if point_values else None,
                      'rms_px':math.sqrt(sum(v*v for v in point_values)/len(point_values)) if point_values else None,
                      'valid_ratio':window_valid/window_total if window_total else 0,
                      'valid_frames':window_valid,'total_frames':window_total}}


def register_analysis_view(app,manager,root):
    @app.get('/analysis-lab')
    def analysis_page():return send_from_directory(root/'web','analysis.html')

    @app.get('/api/review/<rid>')
    def analysis_review(rid):return jsonify(review(manager.store.path(rid)))

    @app.get('/api/review/<rid>/frame/<int:index>.jpg')
    def analysis_frame(rid,index):
        import cv2
        from .offline import source_video
        path=manager.store.path(rid)
        frames=rows(path/'camera_analysis.jsonl') or rows(path/'camera_frames.jsonl')
        if not 0<=index<len(frames):raise ValueError('영상 프레임 범위를 벗어났습니다.')
        capture=cv2.VideoCapture(str(source_video(path)))
        try:
            capture.set(cv2.CAP_PROP_POS_FRAMES,index);ok,frame=capture.read()
        finally:capture.release()
        if not ok:raise ValueError('원본 영상 프레임을 읽을 수 없습니다.')
        for x,y,accepted in frames[index].get('surface_points_px',[]):
            cv2.circle(frame,(round(x),round(y)),2,(40,240,70) if accepted else (30,40,240),-1)
        ok,encoded=cv2.imencode('.jpg',frame,[cv2.IMWRITE_JPEG_QUALITY,90])
        if not ok:raise ValueError('프레임 미리보기 생성 실패')
        return Response(encoded.tobytes(),mimetype='image/jpeg')

