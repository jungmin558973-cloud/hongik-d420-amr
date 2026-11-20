"""Proxy to the EXISTING teleop owner. No serial port or new ROS publisher."""
import math
import secrets
import threading
import time
import requests


class TeleopProxy:
    def __init__(self,url,manager):
        self.url=url;self.manager=manager;self.http=requests.Session();self.http.trust_env=False
        self.lock=threading.RLock();self.enabled=False;self.owner=None;self.control_id=None
        self.seq=-1;self.last_input=0.;self.client=None;self.error='';self.extended=False
        self.token=None;self.closed=threading.Event()
        threading.Thread(target=self.watchdog,daemon=True).start()

    def get(self):
        r=self.http.get(self.url+'/api/status',timeout=(.5,1));r.raise_for_status();return r.json()

    def call(self,op,data):
        if self.token is None:
            r=self.http.get(self.url+'/api/session',timeout=(.5,1));r.raise_for_status();self.token=r.json()['token']
        r=self.http.post(self.url+'/api/'+op,json=data,headers={'X-Hongik-Control':self.token},timeout=(.5,1))
        if not r.ok:raise ValueError(r.json().get('error','Existing control service rejected command'))
        return r.json()

    def arm(self,data):
        from .runtime import CHECKS
        with self.lock:
            if getattr(self.manager,'analysis_running',False) or getattr(self.manager,'saving',False):raise ValueError('Wait for save/offline analysis before manual control')
            if self.manager.active and not self.manager.record_only:raise ValueError('Automatic experiment owns control')
            if self.enabled:raise ValueError('Teleop already armed')
            if any(data.get('checks',{}).get(k) is not True for k in CHECKS):raise ValueError('Complete local safety checklist')
            state=self.get()
            if state.get('active'):raise ValueError('Existing drive owns control; stop it in its own window first')
            if not state.get('allow_real') or not state.get('connected'):raise ValueError('Existing robot control is not connected; open original connection screen')
            if getattr(getattr(self.manager,'backend',None),'can_arm',False):self.manager.backend.release()
            self.owner=secrets.token_urlsafe(24);self.extended=data.get('extended_ack') is True
            result=self.call('teleop-start',{'mode':'real','owner':self.owner,'config':{'speed':.12}})
            self.control_id=result['state']['control_id'];self.client=secrets.token_urlsafe(24)
            self.seq=-1;self.last_input=time.perf_counter();self.enabled=True;self.error=''
            return {'client':self.client,'speed_limit':1.5 if self.extended else .12,'yaw_limit':5.4 if self.extended else .3}

    def input(self,data):
        with self.lock:
            if not self.enabled or data.get('client')!=self.client:raise ValueError('ARM teleop in this window first')
            if self.manager.active and not self.manager.record_only:self.stop();raise ValueError('Automatic/manual ownership conflict')
            seq=data.get('sequence');v=data.get('speed');w=data.get('yaw_speed')
            if type(seq) is not int or seq<=self.seq:raise ValueError('Stale teleop packet')
            for n,limit in ((v,1.5 if self.extended else .12),(w,5.4 if self.extended else .3)):
                if type(n) not in (float,int) or not math.isfinite(n) or not .03<=n<=limit:raise ValueError('Speed exceeds selected manual envelope')
            if any(type(data.get(k)) is not int or data[k] not in (-1,0,1) for k in ('forward','turn')):raise ValueError('Invalid direction')
            self.seq=seq;self.last_input=time.perf_counter()
            result=self.call('teleop-input',{'owner':self.owner,'control_id':self.control_id,'sequence':seq,
                         'forward':data['forward'],'turn':data['turn'],'speed':v,'yaw_speed':w})
            self.call('heartbeat',{'owner':self.owner});return {'ok':True}

    def stop(self,force=False):
        with self.lock:
            was=self.enabled;self.enabled=False;self.client=None
            if was or force:
                try:self.call('stop',{})
                except Exception:self.error='STOP acknowledgement unavailable; use physical STOP'

    def watchdog(self):
        while not self.closed.wait(.05):
            if self.enabled and time.perf_counter()-self.last_input>.30:self.stop()

    def status(self):return {'enabled':self.enabled,'error':self.error,'extended':self.extended,
                            'input_age_s':time.perf_counter()-self.last_input if self.enabled else None,
                            'input_ceiling_m_s':1.5,'input_ceiling_rad_s':5.4,'hardware_limits_verified':False}

    def close(self):self.stop();self.closed.set()
