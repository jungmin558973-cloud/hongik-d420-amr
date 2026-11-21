"""Peak liquid-rise frame and nearby raw telemetry, retaining clock uncertainty."""
import csv
from .storage import dump,csv_write


def export_peak(path,frames,robot,events,clock):
    brakes=[e for e in events if e.get('segment')=='forward' and e.get('event')=='BRAKE_REQUEST' and 'utc' in e]
    stop=next((e for e in events if e.get('segment')=='forward' and e.get('event')=='STOP_CONFIRMED' and 'utc' in e),None)
    start=brakes[0]['utc']-.5 if brakes else None
    end=stop['utc']+10 if stop else None
    candidates=[]
    for f in frames:
        if not f.get('valid') or f.get('segment','forward')!='forward':continue
        if start is not None and f['utc']<start:continue
        if end is not None and f['utc']>end:continue
        values=[v for v in f.get('profile_displacement_px',[]) if v is not None]
        if values:candidates.append((max(values),f))
    if not candidates:
        result={'available':False,'reason':'No valid point-based liquid surface in the stop window'}
        dump(path/'peak_event.json',result);return result
    rise,frame=max(candidates,key=lambda p:p[0]);t=frame['utc'];offset=clock.get('jetson_minus_windows_s',0.)
    result={'available':True,'peak_rise_px':rise,'windows_utc':t,'frame_seq':frame['seq'],
            'stop_window_confirmed':bool(brakes and stop),'clock_quality':clock.get('quality','unknown'),
            'sync_uncertainty_s':clock.get('uncertainty_s'),'physical_height_mm':None,
            'note':'Image-plane rise above stationary baseline; no uncalibrated mm claim'}
    dump(path/'peak_event.json',result)
    csv_write(path/'peak_robot_window.csv',[{**r,'relative_peak_s':r['utc']-t} for r in robot if abs(r['utc']-t)<=2])
    imu=[]
    if (path/'imu.csv').exists():
        with (path/'imu.csv').open(encoding='utf-8-sig',newline='') as f:
            for row in csv.DictReader(f):
                try:delta=float(row['utc'])-offset-t
                except (ValueError,TypeError):continue
                if abs(delta)<=2:imu.append({**row,'relative_peak_s':delta,'clock_quality':result['clock_quality']})
    csv_write(path/'peak_imu_window.csv',imu)
    points=[]
    for x,y,valid in frame.get('surface_points_px',[]):points.append({'x_px':x,'y_px':y,'valid':valid,'utc':t})
    csv_write(path/'peak_surface_points.csv',points)
    import cv2
    from .offline import source_video
    capture=cv2.VideoCapture(str(source_video(path)))
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES,frame['seq']);ok,image=capture.read()
        if ok:
            for x,y,valid in frame.get('surface_points_px',[]):
                cv2.circle(image,(round(x),round(y)),1,(0,255,0) if valid else (0,0,255),-1)
            cv2.imwrite(str(path/'peak_frame.png'),image)
    finally:capture.release()
    return result
