"""Mock plant and SSH-tunnel HTTP adapter share a narrow contract."""
from dataclasses import asdict
import io
import json
import math
from pathlib import Path
import time
import zipfile
import requests
import threading
import os
from .core import Controller,Validation,Observation,Config
from .sync import estimate
from .storage import Journal


class CameraOnlyBackend:
    """Real Windows camera without a robot connection; no synthetic sensor values."""
    simulated=False
    can_arm=False
    mode='CAMERA_ONLY (real camera; robot disconnected)'
    def __init__(self):self.validation=Validation();self.status={}
    def arm(self,checks):raise ValueError('Connect Jetson before ARM; camera-only cannot move robot')
    def release(self):return None
    def clock(self):return {'quality':'unknown','uncertainty_s':1.,'jetson_minus_windows_s':0.,'reason':'Windows camera only; no Jetson time source'}
    def record(self,rid,path):raise ValueError('Jetson not connected; no robot log fabricated')
    def start(self,*args):raise ValueError('Camera-only has no drive output')
    def stop(self,*args):return None
    def poll(self):return {'utc':time.time(),'checks':{'live':False,'sensors':False},'record_error':'','connection_error':'Jetson not connected; use original robot STOP for chassis','state':'READY'}
    def stop_record(self):return None
    def download(self,*args):return None


class MockBackend:
    simulated=True
    def __init__(self):
        self.validation=Validation(.4,2.,True,('OFF','LEVEL','PREDICTIVE'),True,'SYNTHETIC ONLY')
        self.control=Controller(self.validation);self.obs=Observation();self.last=time.perf_counter();self.journal=None
        self.event_index=0;self.fault='';self.seq=0;self.status={};self.logged=False
        self.io_lock=threading.Lock();self.files={};self.control_lock=threading.RLock()
    def arm(self,checks):return self.control.arm(time.perf_counter(),checks)
    def clock(self):return {'jetson_minus_windows_s':0.,'uncertainty_s':.001,'quality':'good','probes':9,'source':'mock common clock'}
    def record(self,rid,path):
        self.journal=Journal(path)
        self.files={k:(Path(path)/k).open('a',encoding='utf-8') for k in ('robot_telemetry.jsonl','events.jsonl')}
        self.logged=True
    def start(self,c,rid,token):
        with self.control_lock:self.control.start(c,rid,token,time.perf_counter(),self.obs)
    def beat(self,seq):self.control.beat(seq,time.perf_counter())
    def stop(self,reason='Operator STOP'):self.control.abort(reason,time.perf_counter())
    def release(self):
        if self.control.active or abs(self.obs.v)>.008:raise ValueError('Stop first')
        self.control.stop_latched=False;self.control.state='READY'
    def poll(self):
        with self.control_lock:return self._poll()
    def _poll(self):
        now=time.perf_counter();dt=min(.1,max(.001,now-self.last));self.last=now
        self.obs.v+=(self.control.command-self.obs.v)*min(1.,dt/.10)
        self.obs.x+=self.obs.v*dt
        self.obs.live=self.fault!='odom';self.obs.sensors=self.fault!='sensor';self.obs.tray_ok=self.fault!='tray'
        self.obs.clear_front=self.fault!='obstacle';self.obs.recorder_ok=self.fault!='disk'
        if self.fault=='network':raise ValueError('Injected network loss')
        command=self.control.tick(now,self.obs) if self.control.active else 0.
        c=self.control;utc=time.time();self.seq+=1
        row={'utc':utc,'mono':now,'seq':self.seq,'x':self.obs.x,'y':0.,'v':self.obs.v,
             'requested_v':command,'final_v':command,'state':c.state,'segment':c.segment,
             'roll_deg':.2*math.sin(now) if hasattr(c,'c') and c.c.tray!='OFF' else None,
             'pitch_deg':.2*math.cos(now) if hasattr(c,'c') and c.c.tray!='OFF' else None,
             'simulated':True}
        with self.io_lock:
            if self.journal and self.logged:
                if c.segment=='forward':self.files['robot_telemetry.jsonl'].write(json.dumps(row)+'\n')
                for e in c.events[self.event_index:]:self.files['events.jsonl'].write(json.dumps({**e,'utc':utc+e['mono']-now,'source':'mock'})+'\n')
                self.event_index=len(c.events)
        self.status={**row,'reason':c.reason,'checks':asdict(self.obs),'validation':asdict(self.validation),'recording':self.logged,'record_error':'','motion_enabled':False}
        return self.status
    def stop_record(self):
        with self.io_lock:
            self.logged=False
            for f in self.files.values():f.flush();os.fsync(f.fileno());f.close()
            self.files={}
    def download(self,rid,path):return None


class RemoteBackend:
    simulated=False
    independent_heartbeat=True
    def __init__(self,url,token):
        if not url.startswith(('http://127.0.0.1:','http://localhost:')):raise ValueError('Jetson agent must use local SSH tunnel')
        if not token or len(token)<32:raise ValueError('Set D420_AGENT_TOKEN (>=32 chars)')
        self.url=url.rstrip('/');self.session=requests.Session();self.session.headers['Authorization']='Bearer '+token
        self.session.trust_env=False;self.can_arm=False
        self.validation=Validation();self.status={}
    def call(self,route,data=None):
        deadline=30 if route.startswith('/record/') else 3
        r=self.session.get(self.url+route,timeout=(1,2)) if data is None else self.session.post(self.url+route,json=data,timeout=(1,deadline))
        d=r.json()
        if not r.ok:raise ValueError(d.get('error','Jetson unavailable'))
        return d
    def arm(self,checks):return self.call('/control/arm',{'checks':checks})['arm_token']
    def clock(self):
        probes=[]
        for _ in range(3):
            a=time.time();r=self.call('/clock');d=time.time();probes.append((a,r['receive_utc'],r['send_utc'],d))
        result=estimate(probes);result['measured_windows_utc']=time.time();self.clock_offset=result['jetson_minus_windows_s'];return result
    def record(self,rid,path):
        try:self.call('/record/start',{'run_id':rid})
        except (requests.Timeout,requests.ConnectionError):
            # Recover an accepted recording ACK; never resend START or steal
            # another recording after an uncertain network response.
            state=self.call('/status')
            if not (state.get('recording') and state.get('recording_run_id')==rid and not state.get('record_error')):raise
    def start(self,c,rid,token):
        self.status['run_id']=rid
        config=asdict(c)
        if not self.status.get('capabilities',{}).get('s_curve_strength'):
            if c.s_curve and c.s_curve_strength not in (0,100):raise ValueError('Jetson 제어 모듈 업데이트가 필요합니다. S-curve 0/100은 기존 방식으로 사용 가능합니다.')
            config.pop('s_curve_strength',None)
        self.call('/control/start',{'config':config,'run_id':rid,'arm_token':token})
    def beat(self,seq):
        if not hasattr(self,'beat_session'):
            self.beat_session=requests.Session();self.beat_session.trust_env=False
            self.beat_session.headers.update(self.session.headers)
        r=self.beat_session.post(self.url+'/control/beat',json={'run_id':self.status.get('run_id'),'seq':seq},timeout=(.3,.3))
        r.raise_for_status()
    def stop(self,reason='Operator STOP'):
        if self.status.get('control_available',True):return self.call('/control/stop',{})
        # Reuse the verified existing emergency-stop API when only recorder is installed.
        config=Path(os.environ.get('D420_CONNECTION_FILE',str(Path(__file__).resolve().parents[1]/'connection.json')))
        settings=json.loads(config.read_text(encoding='utf-8'))
        url='http://127.0.0.1:'+str(int(settings['local_port']))
        session=self.session.get(url+'/api/session',timeout=(1,2)).json()
        response=self.session.post(url+'/api/stop',json={},headers={'X-Hongik-Control':session['token']},timeout=(1,3))
        if not response.ok:raise ValueError('Existing robot STOP acknowledgement unavailable; use physical STOP')
        return response.json()
    def release(self):
        if self.status.get('control_available'):self.call('/control/release',{})
    def poll(self):
        self.status=self.call('/status')
        self.can_arm=bool(self.status.get('motion_enabled') and self.status.get('control_available'))
        d=self.status.get('validation')
        if d:self.validation=Validation(**d)
        stamp=self.status.get('utc',self.status.get('agent_utc',0))
        if time.time()-(stamp-getattr(self,'clock_offset',0.))>1.:raise ValueError('Stale Jetson status / clock mismatch; resync required')
        if not self.status.get('control_available'):
            self.status['checks']={'live':self.status.get('odom_age',999)<.3,'sensors':False}
            self.status['state']='RECORDING_ONLY'
        return self.status
    def stop_record(self):self.call('/record/stop',{})
    def download(self,rid,path):
        # Stream to disk; preserve Jetson originals if network drops.
        archive=Path(path)/'jetson_raw.zip'
        with self.session.get(self.url+'/archive/'+rid,stream=True,timeout=(2,120)) as r:
            r.raise_for_status()
            with archive.open('wb') as f:
                for b in r.iter_content(1024*1024):f.write(b)
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                target=(Path(path)/info.filename).resolve()
                if not target.is_relative_to(Path(path).resolve()):raise ValueError('Unsafe archive path')
                if info.filename=='events.jsonl':
                    with (Path(path)/'events.jsonl').open('ab') as f:f.write(z.read(info))
                else:z.extract(info,path)
