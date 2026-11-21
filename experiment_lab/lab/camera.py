"""Crash/hang isolated capture worker. The web server never calls VideoCapture."""
import csv
import json
import multiprocessing as mp
import os
from pathlib import Path
import queue
import time


def worker(commands,updates,options,preview):
    import cv2
    from .vision import Tracker,synthetic_frame
    cv2.setNumThreads(1)
    capture=None;raw=annotated=None;frame_log=None;run=None;seq=0
    started=time.perf_counter();origin_utc=time.time();tracker=Tracker(options.get('roi'));record_priority=False
    fps=options.get('fps',30);period=1/fps;record_start=None;last=None;dropped=0
    def emit(data):
        try:updates.put_nowait(data)
        except queue.Full:
            try:updates.get_nowait()
            except queue.Empty:pass
            try:updates.put_nowait(data)
            except queue.Full:pass
    def close():
        nonlocal raw,annotated,frame_log,run
        for writer in (raw,annotated):
            if writer:writer.release()
        if frame_log:frame_log.flush();os.fsync(frame_log.fileno());frame_log.close()
        raw=annotated=frame_log=None;run=None
    try:
        if options.get('source','synthetic')!='synthetic':
            source=options.get('source');backend=options.get('backend','DSHOW')
            if isinstance(source,int) and backend=='DSHOW' and not options.get('device_name'):
                try:
                    from .devices import enumerate_devices
                    device=next(d for d in enumerate_devices() if d['index']==source)
                    options.update(device_name=device['name'],identity_verified=True)
                except Exception:options['device_name']='Unverified device '+str(source)
            api={'DSHOW':cv2.CAP_DSHOW,'MSMF':cv2.CAP_MSMF,'ANY':cv2.CAP_ANY}.get(backend,cv2.CAP_ANY)
            emit({'ready':False,'error':'Opening '+options.get('device_name',str(source))+' / '+backend,'stage':'open'})
            capture=cv2.VideoCapture(source,api if isinstance(source,int) else cv2.CAP_ANY)
            if not capture.isOpened():raise RuntimeError(f'Camera {source} / {backend}: open failed. Close Teams/Zoom/browser camera; try another index/backend.')
            if options.get('configure') is True:
                emit({'ready':False,'error':'Configuring camera resolution','stage':'configure'})
                capture.set(cv2.CAP_PROP_FRAME_WIDTH,options.get('width',1280));capture.set(cv2.CAP_PROP_FRAME_HEIGHT,options.get('height',720))
                capture.set(cv2.CAP_PROP_FPS,fps)
                # DirectShow may reset subtype while resizing. Set MJPG LAST;
                # uncompressed 1080p YUY2 on this USB camera is limited to 5fps.
                capture.set(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*'MJPG'))
            emit({'ready':False,'error':'Waiting for first camera frame','stage':'first_frame'})
        while True:
            loop=time.perf_counter()
            try:
                while True:
                    cmd=commands.get_nowait();op=cmd['op']
                    if op=='exit':return
                    if op=='roi':
                        tracker.reset(cmd['settings'])
                        (Path(preview).parent/'reference.json').unlink(missing_ok=True)
                    if op=='reference':
                        if tracker.settings.get('method')!='marker_blue':raise ValueError('Marker-blue mode required for stationary reference')
                        from .marker_blue import MarkerBlue
                        if not hasattr(tracker,'marker_blue'):tracker.marker_blue=MarkerBlue(tracker.settings)
                        tracker.marker_blue.begin_reference()
                    if op=='seek' and capture is not None and isinstance(options.get('source'),str):
                        capture.set(cv2.CAP_PROP_POS_MSEC,cmd['seconds']*1000);tracker.template=None
                    if op=='record':
                        close();run=cmd['path'];record_start=time.perf_counter();seq=0;tracker.reset(tracker.settings);record_priority=cmd.get('record_priority',True)
                    if op=='stop':close();emit({'closed':True})
            except queue.Empty:pass
            mono=time.perf_counter();utc=origin_utc+mono-started
            if capture is None:frame=synthetic_frame(mono-record_start if run and record_start else (mono-started)%12.)
            else:
                ok,frame=capture.read()
                if not ok or frame is None:raise RuntimeError('Camera read failed/disconnected or replay EOF; recorded data preserved')
            capture_at=time.perf_counter();elapsed=mono-started
            video_s=None
            if capture is not None and isinstance(options.get('source'),str):
                video_s=capture.get(cv2.CAP_PROP_POS_MSEC)/1000.;elapsed=video_s
            if run and record_priority:
                result={'valid':False,'displacement_px':None,'reason':'Record priority: analysis deferred','analysis_deferred':True};overlay=frame
            else:result,overlay=tracker.process(frame,elapsed)
            if result.get('reference_locked') and tracker.settings.get('reference_profile'):
                reference_path=Path(preview).parent/'reference.json'
                if not reference_path.exists():
                    from .storage import dump
                    dump(reference_path,{'settings':tracker.settings,'camera_name':options.get('device_name'),'frame_size':[frame.shape[1],frame.shape[0]],'utc':utc})
            if run and raw is None:
                shape=(frame.shape[1],frame.shape[0]);fourcc=cv2.VideoWriter_fourcc(*'mp4v')
                # Intraframe MJPEG avoids low-bitrate MPEG4 as analysis evidence.
                raw_name='webcam_raw.avi' if record_priority else 'webcam_raw.mp4'
                raw=cv2.VideoWriter(str(Path(run)/raw_name),cv2.VideoWriter_fourcc(*'MJPG') if record_priority else fourcc,fps,shape)
                from .storage import dump
                dump(Path(run)/'video_capture.json',{'file':raw_name,'codec':'MJPG' if record_priority else 'mp4v','requested_fps':fps,'width':shape[0],'height':shape[1],'timing':'camera_frames.jsonl; container fps is nominal','record_priority':record_priority})
                annotated=None if record_priority else cv2.VideoWriter(str(Path(run)/'webcam_annotated.mp4'),fourcc,fps,shape)
                if not raw.isOpened() or (annotated is not None and not annotated.isOpened()):raise RuntimeError('Video encoder cannot open output')
                if tracker.settings.get('reference_profile'):
                    from .storage import dump
                    dump(Path(run)/'stationary_reference.json',tracker.settings)
                frame_log=(Path(run)/'camera_frames.jsonl').open('a',encoding='utf-8')
                emit({'recording':True,'path':run})
            interval=mono-last if last else period;last=mono
            dropped+=max(0,round(interval/period)-1)
            row={'seq':seq,'utc':utc,'monotonic':mono,'host_utc_observed':time.time(),
                 'actual_fps':1/interval if interval>0 else None,'capture_read_ms':(capture_at-mono)*1000,
                 'processing_ms':(time.perf_counter()-capture_at)*1000,'estimated_dropped':dropped,
                 'segment':'forward','source_video_s':video_s,'frame_width':frame.shape[1],'frame_height':frame.shape[0],**result}
            if run:
                raw.write(frame)
                if annotated is not None:annotated.write(overlay)
                frame_log.write(json.dumps(row,allow_nan=False)+'\n')
                # A camera worker crash should cost at most one second of frame metadata.
                if seq%30==0:
                    frame_log.flush();os.fsync(frame_log.fileno())
                    import shutil
                    if shutil.disk_usage(run).free<512*1024**2:raise RuntimeError('Disk below 512MiB reserve; recording stopped, raw data preserved')
            seq+=1
            ok,jpg=cv2.imencode('.jpg',overlay) if not run or seq%6==0 else (False,None)
            if ok:
                tmp=Path(str(preview)+'.tmp');tmp.write_bytes(jpg.tobytes())
                try:os.replace(tmp,preview)
                except PermissionError:pass # Windows viewer may briefly hold old preview; never stop recording.
            emit({'ready':True,'error':'','recording':bool(run),'utc':utc,'monotonic':mono,'seq':seq,
                  'source':options.get('source'),'device_name':options.get('device_name',str(options.get('source'))),
                  'backend':options.get('backend'),'identity_verified':options.get('identity_verified',False),
                  'width':frame.shape[1],'height':frame.shape[0],'fps':row['actual_fps'],
                  'capture_read_ms':row['capture_read_ms'],'processing_ms':row['processing_ms'],
                  'dropped':dropped,'measurement':result})
            time.sleep(max(0.,period-(time.perf_counter()-loop)))
    except BaseException as exc:
        emit({'ready':False,'recording':False,'error':type(exc).__name__+': '+str(exc)})
    finally:
        close()
        if capture:capture.release()


class Camera:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.preview=self.root/'preview.jpg'
        self.process=None;self.status={'ready':False,'error':'Camera not started'};self.received=0.

    def start(self,options):
        self.close();ctx=mp.get_context('spawn');self.commands=ctx.Queue(16);self.updates=ctx.Queue(3)
        try:self.preview.unlink(missing_ok=True)
        except PermissionError:pass
        self.status={'ready':False,'error':'Opening camera (8s timeout)'};self.received=time.perf_counter()
        self.process=ctx.Process(target=worker,args=(self.commands,self.updates,options,str(self.preview)),daemon=True)
        self.process.start()

    def poll(self):
        if self.process:
            try:
                while True:self.status.update(self.updates.get_nowait());self.received=time.perf_counter()
            except queue.Empty:pass
            age=time.perf_counter()-self.received
            if not self.process.is_alive():self.status.update(ready=False,error=self.status.get('error') or 'Capture process exited; reconnect camera')
            elif age>(60 if self.status.get('stage')=='configure' else 8):
                self.process.terminate();self.process.join(1);self.status.update(ready=False,recording=False,error='Capture timed out; worker terminated. Try DSHOW/MSMF/index; server alive.')
            self.status['preview_age_ms']=age*1000
            if age>1:self.status['ready']=False
        return dict(self.status)

    def send(self,op,**data):
        if not self.process or not self.process.is_alive():raise ValueError('Camera worker not running')
        self.commands.put_nowait({'op':op,**data})

    def record(self,path,record_priority=False):
        self.status['recording']=False;self.send('record',path=str(path),record_priority=record_priority);deadline=time.perf_counter()+4
        while time.perf_counter()<deadline:
            if self.poll().get('recording'):return
            if not self.process.is_alive():break
            time.sleep(.03)
        raise ValueError('Camera recording acknowledgement timeout')

    def stop_record(self):
        if self.process and self.process.is_alive():
            self.status['closed']=False;self.send('stop');deadline=time.perf_counter()+3
            while time.perf_counter()<deadline:
                if self.poll().get('closed'):return
                time.sleep(.02)
            raise ValueError('Camera close acknowledgement timeout')

    def close(self):
        if self.process:
            if self.process.is_alive():
                try:self.send('exit')
                except (ValueError,queue.Full):pass
                self.process.join(2)
            if self.process.is_alive():self.process.terminate();self.process.join(1)
            self.process=None
