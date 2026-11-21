"""Windows orchestration; hardware watchdog lives independently on Jetson."""
from dataclasses import asdict
import os
from pathlib import Path
import shutil
import threading
import time
from .core import Config
from .camera import Camera
from .storage import Store,Journal,dump
from .drive import UploadQueue
from .vision import SYNTHETIC_ROI
from .analysis import compare,recommend


CHECKS=('space_clear','physical_stop_ready','cup_secure','origin_checked','operator_present')


class Manager:
    def __init__(self,root,backend,autostart=True):
        self.store=Store(root);self.recovered=self.store.recover();self.backend=backend
        self.camera=Camera(Path(root)/'_camera');self.uploads=UploadQueue(self.store)
        self.lock=threading.RLock();self.closed=threading.Event();self.active=False;self.record_only=False
        self.run_id=None;self.path=None;self.token=None;self.seq=0;self.browser_at=0.
        self.state='READY';self.error='';self.robot={};self.clock={'quality':'unknown','uncertainty_s':1.,'jetson_minus_windows_s':0.}
        self.saving=False;self.analysis_running=False;self.measurement_closed=False;self.saved=False;self.history=[];self.metrics={}
        self.remote_recording=False;self.worker=None;self.save_thread=None
        self.heartbeat_thread=None
        self.start_step='';self.stop_generation=0;self.starting=False
        self.quality_tail_started=None;self.quality_stop_since=None;self.quality_stop_utc=None
        self.camera_reconnecting=False;self.last_camera_retry=0.;self.auto_camera_requested=False
        self.recovery_log=None;self.recovery_last_state=None
        self.roi=SYNTHETIC_ROI.copy() if backend.simulated else {'method':'marker_blue','liquid_roi':[0,0,8,8],'anchor_roi':[0,0,8,8],'marker_ids':list(range(8)),'hsv_lower':[90,80,40],'hsv_upper':[135,255,255]}
        if autostart:
            self.worker=threading.Thread(target=self.loop,daemon=True);self.worker.start()
            self.upload_thread=threading.Thread(target=self.upload_loop,daemon=True);self.upload_thread.start()
            if getattr(backend,'independent_heartbeat',False):
                self.heartbeat_thread=threading.Thread(target=self.heartbeat_loop,daemon=True);self.heartbeat_thread.start()

    def start_camera(self,options):
        if self.active or self.saving or self.analysis_running:raise ValueError('Cannot replace camera during a run/save/analysis')
        old_name=getattr(self,'camera_options',{}).get('device_name')
        if options.get('source')!='synthetic' and getattr(self,'camera_source',None) is not None and options.get('source')!=self.camera_source and old_name!=options.get('device_name'):self.roi={}
        self.camera_source=options.get('source')
        opts={'source':'synthetic','fps':30,'width':1920,'height':1080,'roi':self.roi,**options}
        if isinstance(opts['source'],int):opts.setdefault('configure',True)
        reference_file=self.camera.root/'reference.json'
        if opts['source']!='synthetic' and reference_file.exists():
            import json
            saved=json.loads(reference_file.read_text(encoding='utf-8'))
            if saved.get('camera_name')==opts.get('device_name') and saved.get('frame_size')==[opts['width'],opts['height']]:
                old=saved.get('settings',{})
                if all(old.get(k)==self.roi.get(k) for k in ('method','hsv_lower','hsv_upper','marker_ids')):
                    for k in ('reference_profile','reference_width'):self.roi[k]=old[k]
        if self.backend.simulated and opts['source']!='synthetic':self.roi={};opts['roi']=self.roi
        self.camera.start(opts)
        self.camera_options=opts

    def browser_beat(self):self.browser_at=time.perf_counter()

    def arm(self,checks):
        if self.starting:raise ValueError('실험 시작 준비 중입니다. 중복 시작하지 마세요.')
        if self.active or self.saving or self.analysis_running:raise ValueError('Run/save/analysis already active')
        if not self.camera.poll().get('ready'):raise ValueError('Camera not ready')
        if any(checks.get(k) is not True for k in CHECKS):raise ValueError('Complete safety checklist')
        self.token=self.backend.arm(checks);self.state='ARMED';self.browser_beat()

    def start(self,data,record_only=False):
        with self.lock:
            if self.starting:raise ValueError('실험 시작 준비 중입니다.')
            self.starting=True
        try:return self._start(data,record_only)
        finally:self.starting=False

    def _start(self,data,record_only=False):
        with self.lock:
            if self.active or self.saving or self.analysis_running:raise ValueError('Run/save/analysis already active')
            c=Config.parse(data)
            if not record_only:
                if not self.token:raise ValueError('ARM first')
                self.backend.validation.check(c)
                if not c.record_priority and self.roi.get('method')=='marker_blue' and not self.camera.poll().get('measurement',{}).get('reference_locked'):
                    raise ValueError('Capture stationary reference at stop position before automatic trial')
            if not self.camera.poll().get('ready'):raise ValueError('Camera not ready')
            if not self.backend.simulated and not record_only and c.record_priority:
                camera=self.camera.poll()
                if camera.get('width',0)<1920 or camera.get('height',0)<1080:raise ValueError('Native 1920x1080 capture required; reconnect C920 in maximum-quality mode')
            if not record_only and c.require_4k:
                camera=self.camera.poll()
                if camera.get('width',0)<3840 or camera.get('height',0)<2160:
                    raise ValueError('Research protocol requires native >=3840x2160. Current camera is not 4K; preview/record-only is preliminary only.')
            if shutil.disk_usage(self.store.root).free<512*1024**2:raise ValueError('Need at least 512 MiB free')
            if hasattr(self.backend,'recover_closed_recording'):
                self.start_step='이전 실험 로그 정리';self.backend.recover_closed_recording(self.store)
            self.start_step='시각 동기화';generation=self.stop_generation
            try:
                if self.clock.get('quality')!='good' or time.time()-self.clock.get('measured_windows_utc',0)>30:self.clock=self.backend.clock()
            except Exception:
                if not record_only:raise
                self.clock={'quality':'unknown','uncertainty_s':1.,'jetson_minus_windows_s':0.,'reason':'Jetson unavailable; camera-only recording'}
            if not record_only and not c.record_priority and self.clock['uncertainty_s']>.1:raise ValueError('Clock uncertainty >100ms; check SSH/NTP')
            stored_config=asdict(c)
            if record_only:stored_config['scenario']='record_only'
            self.run_id,self.path=self.store.begin(stored_config,self.backend.simulated);self.journal=Journal(self.path)
            self.metrics={};self.error='';self.saved=False;self.measurement_closed=False;self.remote_recording=False
            self.quality_tail_started=self.quality_stop_since=self.quality_stop_utc=None
            self.record_only=record_only;self.seq=0;self.history=[]
            self.recovery_log=None;self.recovery_last_state=None
            dump(self.path/'roi.json',self.roi);dump(self.path/'clock.json',self.clock)
            dump(self.path/'camera_device.json',getattr(self,'camera_options',{}))
            try:
                if generation!=self.stop_generation:raise ValueError('Startup cancelled by STOP')
                self.start_step='영상 녹화 시작'
                self.camera.record(self.path,record_priority=c.record_priority)
                self.start_step='Jetson 원시 로그 시작'
                try:self.backend.record(self.run_id,self.path);self.remote_recording=True
                except Exception as e:
                    if not record_only:raise
                    self.journal.append('events.jsonl',{'utc':time.time(),'event':'ROBOT_UNAVAILABLE','reason':type(e).__name__})
                self.browser_beat()
                if generation!=self.stop_generation:raise ValueError('Startup cancelled by STOP')
                self.start_step='자동 주행 명령 전달'
                if not record_only:self.backend.start(c,self.run_id,self.token)
                if generation!=self.stop_generation:raise ValueError('Startup cancelled by STOP')
                self.active=True;self.state='RECORDING';self.token=None
                self.start_step=''
                self.journal.append('events.jsonl',{'utc':time.time(),'event':'WINDOWS_RECORDING_ACK','clock':self.clock})
            except Exception:
                try:self.backend.stop('Start failed')
                except Exception:pass
                try:self.camera.stop_record()
                except Exception:pass
                if not self.remote_recording:
                    try:
                        remote=self.backend.poll()
                        self.remote_recording=bool(remote.get('recording') and remote.get('recording_run_id')==self.run_id)
                    except Exception:pass
                if self.remote_recording:
                    try:self.backend.stop_record();self.backend.download(self.run_id,self.path)
                    except Exception:pass
                self.store.finalize(self.run_id,'ABORTED',self.clock);self.state='ABORTED';raise
            return self.run_id

    def stop(self,reason='Operator safety stop'):
        # Called independently from UI STOP; never wait for file/analysis lock.
        self.token=None;self.stop_generation+=1
        try:
            self.backend.stop(reason)
        finally:
            self.error=reason
            if self.active:self.finish('ABORTED' if not self.record_only else 'COMPLETE')
            else:self.state='ABORTED'

    def end_measurement(self):
        if self.measurement_closed:return
        self.measurement_closed=True
        errors=[]
        try:self.camera.stop_record()
        except Exception as e:errors.append('Camera close: '+str(e))
        if self.remote_recording:
            try:self.backend.stop_record();self.backend.download(self.run_id,self.path)
            except Exception as e:errors.append('Jetson artifacts pending: '+type(e).__name__)
        self.journal.append('events.jsonl',{'utc':time.time(),'event':'MEASUREMENT_END','errors':errors,'return_scope':'recovery only'})
        if errors:self.error='; '.join(errors)
        if not self.backend.simulated:
            try:
                end_clock=self.backend.clock()
                self.clock['end_check']=end_clock
                self.clock['offset_drift_s']=end_clock['jetson_minus_windows_s']-self.clock['jetson_minus_windows_s']
                if abs(self.clock['offset_drift_s'])>.02 or end_clock['quality']!='good':self.clock['quality']='uncertain'
            except Exception:self.clock['quality']='uncertain'

    def save(self,status):
        try:
            self.end_measurement()
            self.metrics=self.store.finalize(self.run_id,status,self.clock);self.path=self.store.path(self.run_id);self.journal.path=self.path;self.saved=True
            with self.store.connect() as db:
                queue_status=status if status!='COMPLETE' or self.metrics.get('protocol',{}).get('valid_for_research_protocol') else 'INVALID_PROTOCOL'
                db.execute('UPDATE trial_queue SET status=? WHERE run_id=?',(queue_status,self.run_id))
        except Exception as e:
            self.error='Save failed; local raw files preserved: '+str(e);self.state='ERROR'
        finally:self.saving=False

    def begin_save(self,status):
        if self.saved or self.saving:return
        self.saving=True;self.save_thread=threading.Thread(target=self.save,args=(status,),daemon=True);self.save_thread.start()

    def finish(self,status):
        self.active=False;self.state=status;self.begin_save(status)

    def tick(self):
        cam=self.camera.poll()
        self.retry_camera_if_idle(cam)
        try:self.robot=self.backend.poll()
        except Exception as e:
            self.robot={**self.robot,'connection_error':type(e).__name__+': '+str(e)}
            if hasattr(self.backend,'can_arm'):self.backend.can_arm=False
            if self.active and not self.record_only:self.stop('Jetson connection lost')
            return
        if not self.active:return
        if not cam.get('ready') and not self.measurement_closed:self.stop('Camera lost/worker timed out');return
        if self.record_only:return
        if time.perf_counter()-self.browser_at>.6:self.stop('Browser dead-man heartbeat lost');return
        if self.robot.get('record_error'):self.stop('Jetson raw log fault');return
        if self.heartbeat_thread is None:
            self.seq+=1
            try:self.backend.beat(self.seq)
            except Exception:self.stop('Heartbeat delivery failed');return
        state=self.robot.get('state','ERROR');self.state=state
        if self.recovery_log and state!=self.recovery_last_state:
            self.recovery_log.append(self.run_id+'.jsonl',{'utc':time.time(),'state':state,'reason':self.robot.get('reason'),'purpose':'recovery safety audit only'})
            self.recovery_last_state=state
        self.history.append({k:self.robot.get(k) for k in ('utc','v','requested_v','x','roll_deg','pitch_deg','state')})
        self.history=self.history[-600:]
        if state=='ABORTED' and self.robot.get('reason','').startswith('Actual >=1.5s constant-speed interval'):
            self.preserve_quality_tail();return
        if state=='RETURNING' and not self.measurement_closed:
            recovery_dir=self.store.root/'_recovery';recovery_dir.mkdir(exist_ok=True)
            self.recovery_log=Journal(recovery_dir)
            self.recovery_log.append(self.run_id+'.jsonl',{'utc':time.time(),'state':'RETURNING','purpose':'recovery safety audit only'})
            # Offload encoding/bag close/download/plots; keep servicing recovery heartbeat.
            self.begin_save('COMPLETE')
        elif state in ('COMPLETE','ABORTED','ERROR'):
            self.active=False
            if state=='ABORTED':self.error=self.robot.get('reason','Aborted')
            self.begin_save(state)

    def preserve_quality_tail(self):
        # Compatibility with an older deployed controller: a quality warning at
        # the final stop must not truncate the post-stop camera observation.
        now=time.perf_counter()
        if self.quality_tail_started is None:self.quality_tail_started=now
        fresh=self.robot.get('odom_age',999)<.3 and self.robot.get('checks',{}).get('live') is True
        if fresh and abs(self.robot.get('v',999))<.008:
            if self.quality_stop_since is None:self.quality_stop_since=now
            if now-self.quality_stop_since>=.4 and self.quality_stop_utc is None:
                import math
                origin=next((e.get('origin') for e in self.robot.get('events',[]) if e.get('event')=='RECORDING' and e.get('run_id')==self.run_id),None)
                along=None
                if origin:along=(self.robot['x']-origin[0])*math.cos(origin[2])+(self.robot['y']-origin[1])*math.sin(origin[2])
                self.quality_stop_utc=time.time()
                self.journal.append('events.jsonl',{'event':'STOP_CONFIRMED','segment':'forward','utc':self.quality_stop_utc,'x':along,'run_id':self.run_id,'source':'windows','observer':'fresh actual odometry below 0.008m/s for 0.4s; controller quality abort retained'})
        else:self.quality_stop_since=None
        self.state='SETTLING';self.error=''
        if self.quality_stop_utc is not None and time.time()-self.quality_stop_utc>=10:self.finish('COMPLETE')
        elif self.quality_stop_utc is None and now-self.quality_tail_started>5:self.finish('ABORTED')

    def loop(self):
        while not self.closed.is_set():
            start=time.perf_counter()
            try:self.tick()
            except Exception as e:
                self.error=type(e).__name__+': '+str(e)
                if self.active:
                    try:self.stop('Orchestrator fault')
                    except Exception:pass
            self.closed.wait(max(.001,(.05 if self.active else .25)-(time.perf_counter()-start)))

    def retry_camera_if_idle(self,cam):
        if self.backend.simulated or self.active or self.saving or self.analysis_running or self.camera_reconnecting:return
        options=getattr(self,'camera_options',{})
        if not (self.auto_camera_requested or 'C920' in options.get('device_name','').upper()) or cam.get('ready'):return
        process=self.camera.process
        if process is not None and process.is_alive():return
        if time.perf_counter()-self.last_camera_retry<5:return
        self.camera_reconnecting=True;self.last_camera_retry=time.perf_counter()
        def reconnect():
            try:
                from .devices import resolve
                updated={**options,**resolve('auto','DSHOW')}
                with self.lock:
                    if not self.active and not self.saving and not self.analysis_running:self.start_camera(updated)
            except Exception as e:self.camera.status.update(ready=False,error='C920 reconnect: '+str(e))
            finally:self.camera_reconnecting=False
        threading.Thread(target=reconnect,daemon=True).start()

    def heartbeat_loop(self):
        while not self.closed.is_set():
            if (self.active or (self.starting and self.remote_recording and self.start_step=='자동 주행 명령 전달')) and not self.record_only:
                if time.perf_counter()-self.browser_at>.6:
                    self.stop('Browser dead-man heartbeat lost')
                else:
                    self.seq+=1
                    try:self.backend.beat(self.seq)
                    except Exception:
                        # A single delayed HTTP reply is not proof of a lost
                        # command. The unchanged Jetson watchdog detects loss.
                        pass
            self.closed.wait(.08)

    def upload_loop(self):
        while not self.closed.is_set():
            try:
                if not self.active and not self.saving and not self.analysis_running:self.uploads.once()
            except Exception:pass
            self.closed.wait(2.)

    def status(self):
        if self.error.startswith('Jetson artifacts pending') and self.path and (self.path/'rosbag2/metadata.yaml').exists():
            import json
            manifest=json.loads((self.path/'manifest.json').read_text(encoding='utf-8'))
            if 'rosbag2/metadata.yaml' in manifest.get('files',{}):
                self.error='';self.metrics=json.loads((self.path/'metrics.json').read_text(encoding='utf-8'))
        stop_utc=self.quality_stop_utc
        if stop_utc is None:
            stops=[e for e in self.robot.get('events',[]) if e.get('event')=='STOP_CONFIRMED' and e.get('segment')=='forward' and e.get('run_id')==self.run_id]
            if stops:
                e=stops[-1];stop_utc=self.robot.get('utc',0)+(e['mono']-self.robot.get('mono',0))-self.clock.get('jetson_minus_windows_s',0)
        remaining=max(0,10-(time.time()-stop_utc)) if stop_utc is not None else None
        return {'state':self.state,'active':self.active,'record_only':self.record_only,'run_id':self.run_id,
                'post_stop_remaining_s':remaining,'current_folder':str(self.path) if self.path else None,
                'camera_reconnecting':self.camera_reconnecting,
                'mode':getattr(self.backend,'mode','MOCK' if self.backend.simulated else 'REAL (commissioning required)'),
                'can_arm':getattr(self.backend,'can_arm',True),
                'camera':self.camera.poll(),'robot':self.robot,'clock':self.clock,'error':self.error,
                'saving':self.saving,'analysis_running':self.analysis_running,'metrics':self.metrics,'history':self.history[::5],
                'start_step':self.start_step,
                'starting':self.starting,
                'validation':asdict(self.backend.validation),'disk_free_gb':shutil.disk_usage(self.store.root).free/1024**3,
                'uploads':self.uploads.status(),'recovered':self.recovered,'recovery_only':self.state=='RETURNING'}

    def close(self):
        if self.active:
            try:self.stop('Windows server closing')
            except Exception:pass
        self.closed.set()
        if self.heartbeat_thread:self.heartbeat_thread.join(2)
        if self.worker:self.worker.join(4)
        if self.save_thread:self.save_thread.join(15)
        self.camera.close()
