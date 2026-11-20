"""Pure, deterministic motion planning and safety state machine (SI units)."""
from dataclasses import dataclass, asdict
import math
import secrets
import itertools
import random


def number(v, lo, hi, name):
    if type(v) not in (int, float) or not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError(f'{name}: expected finite {lo}..{hi}')
    return v


@dataclass
class Config:
    distance: float = 1.6
    speed: float = .20
    s_curve: bool = True
    s_curve_strength: float = 100.
    tray: str = 'OFF'
    acceleration: float = .20
    deceleration: float = .25
    jerk: float = .5
    pre_roll: float = 2.
    settle: float = 10.
    post_roll: float = 0.
    return_home: bool = False
    repeats: int = 3
    fill_pct: float = 70.
    cup: str = 'transparent'
    diameter_mm: float = 80.
    water_g: float = 200.
    lid: bool = False
    camera_setting: str = 'native-1080p30-record-priority'
    scenario: str = 'planned_stop'
    notes: str = ''
    lead: float = .20
    gain: float = 1.
    angle_limit: float = 3.
    angle_rate: float = 8.
    ramp: float = .25
    filter_s: float = .10
    center_deg: float = 0.
    brake_offset_m: float = 0.
    min_cruise_s: float = 1.5
    require_4k: bool = False
    record_priority: bool = True
    stop_semantics: str = 'brake_at_distance'
    commissioning: bool = False
    obstacle_stop: bool = True

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or set(data) - set(cls.__dataclass_fields__):
            raise ValueError('Unknown experiment setting')
        c = cls(**data)
        bounds = {'distance':(.05,2.), 'speed':(.03,.4), 'acceleration':(.03,.5),
                  'deceleration':(.03,.6),'jerk':(.05,2.),'pre_roll':(2.,5.),
                  'settle':(3.,30.),'post_roll':(0.,5.),'fill_pct':(1.,95.),
                  'diameter_mm':(10.,200.),'water_g':(1.,2000.),'lead':(0.,.5),
                  'gain':(0.,2.),'angle_limit':(0.,8.),'angle_rate':(.1,12.),
                  'ramp':(.1,2.),'filter_s':(.02,1.),'center_deg':(-2.,2.),
                  'brake_offset_m':(0.,.15),'min_cruise_s':(1.5,10.),'s_curve_strength':(0.,100.)}
        for k,(lo,hi) in bounds.items(): number(getattr(c,k),lo,hi,k)
        for k in ('s_curve','return_home','lid','require_4k','record_priority','commissioning','obstacle_stop'):
            if type(getattr(c,k)) is not bool: raise ValueError(k+' must be boolean')
        if c.s_curve_strength==0:c.s_curve=False
        if not c.s_curve:c.s_curve_strength=0.
        if type(c.repeats) is not int or not 1 <= c.repeats <= 20: raise ValueError('repeats')
        if c.tray not in ('OFF','LEVEL','PREDICTIVE'): raise ValueError('tray')
        if c.stop_semantics not in ('brake_at_distance','stop_at_distance'):raise ValueError('stop_semantics')
        if c.scenario not in ('planned_stop','injected_stop','record_only'): raise ValueError('scenario')
        for k in ('cup','camera_setting','notes'):
            if not isinstance(getattr(c,k),str) or len(getattr(c,k))>2000: raise ValueError(k)
        return c

    @property
    def effective_jerk(self):
        return self.jerk/(self.s_curve_strength/100.) if self.s_curve and self.s_curve_strength>0 else None


@dataclass
class Validation:
    """Local commissioning record, never writable through the browser API."""
    max_speed: float = .12
    max_distance: float = .60
    s_curve: bool = False
    tray_modes: tuple = ('OFF',)
    reverse: bool = False
    evidence: str = 'Initial short test only; revalidate locally'

    def check(self,c):
        if c.commissioning:
            if c.tray!='OFF' or c.return_home or c.scenario!='planned_stop' or c.stop_semantics!='brake_at_distance':
                raise ValueError('직선 촬영은 트레이 OFF·후진 복구 OFF 설정이 필요합니다.')
            return
        if c.speed>self.max_speed or c.distance>self.max_distance:
            raise ValueError('Unvalidated speed/distance; local commissioning record required')
        if c.s_curve and not self.s_curve: raise ValueError('S-curve not commissioned')
        if c.tray not in self.tray_modes: raise ValueError('Tray mode not commissioned')
        if c.return_home and not self.reverse: raise ValueError('Reverse clearance/return not commissioned')


def matrix(repeats=3, seed=None, validation=None):
    rows=[]
    for d,v,s,t in itertools.product((1.6,),(.2,.3,.4),(False,True),('OFF','LEVEL','PREDICTIVE')):
        for n in range(repeats):
            c=Config(distance=d,speed=v,s_curve=s,tray=t)
            reason=''
            try: (validation or Validation()).check(c)
            except ValueError as e: reason=str(e)
            rows.append({'key':f'record-v2-{d}-{v}-{s}-{t}-{n+1}', 'repeat':n+1,
                         'config':asdict(c),'status':'PENDING_VALIDATION' if reason else 'AWAITING_CONFIRMATION',
                         'reason':reason})
    if seed is not None: random.Random(seed).shuffle(rows)
    return rows


class Profile:
    """Analytic 7-segment constant-jerk profile, distance-fit by peak-speed solve.

    OFF uses a trapezoid with discontinuous acceleration (jerk not constrained).
    Each segment stores duration and acceleration/jerk initial values.
    """
    def __init__(self,distance,speed,acceleration,deceleration,jerk,s_curve=True,abrupt=False,strength=100.):
        for v in (distance,speed,acceleration,deceleration,jerk): number(v,1e-6,100.,'profile')
        number(strength,0.,100.,'S-curve strength')
        s_curve=bool(s_curve and strength>0)
        if s_curve:jerk=jerk/(strength/100.)
        self.distance=distance; self.s_curve=s_curve;self.strength=strength if s_curve else 0.
        self.effective_jerk=jerk if s_curve else None
        def ramp(v,a):
            if not s_curve: return [(v/a,a,0.)]
            tj=min(a/jerk,math.sqrt(v/jerk)); ta=max(0.,v/(jerk*tj)-tj)
            return [(tj,0.,jerk),(ta,jerk*tj,0.),(tj,jerk*tj,-jerk)]
        def duration(v,a): return sum(x[0] for x in ramp(v,a))
        def needed(v): return .5*v*(duration(v,acceleration)+(0. if abrupt else duration(v,deceleration)))
        peak=speed
        if needed(peak)>distance:
            lo=0.;hi=speed
            for _ in range(70):
                mid=(lo+hi)/2
                if needed(mid)>distance:hi=mid
                else:lo=mid
            peak=(lo+hi)/2
        self.peak=peak
        cruise=max(0.,(distance-needed(peak))/peak)
        self.cruise_duration=cruise
        self.acceleration_duration=duration(peak,acceleration)
        up=ramp(peak,acceleration)
        down=[] if abrupt else [(dt,-a,-j) for dt,a,j in ramp(peak,deceleration)]
        self.brake_time=duration(peak,acceleration)+cruise
        self.segments=[];t=x=v=0.
        for dt,a,j in up+[(cruise,0.,0.)]+down:
            if dt<=1e-12: continue
            self.segments.append((t,dt,x,v,a,j))
            x+=v*dt+.5*a*dt**2+j*dt**3/6;v+=a*dt+.5*j*dt**2;t+=dt
        self.duration=t

    def at(self,t):
        if t>=self.duration: return self.distance,0.,0.,0.
        t=max(0.,t)
        for start,dt,x,v,a,j in self.segments:
            if t<start+dt:
                u=t-start
                return x+v*u+.5*a*u*u+j*u**3/6,v+a*u+.5*j*u*u,a+j*u,j
        return self.distance,0.,0.,0.


@dataclass
class Observation:
    x: float = 0.
    y: float = 0.
    yaw: float = 0.
    v: float = 0.
    live: bool = True
    sensors: bool = True
    clear_front: bool = True
    clear_rear: bool = True
    tray_ok: bool = True
    recorder_ok: bool = True


class Controller:
    """Motor-owner-side controller. STOP bypasses every motion/tray filter."""
    ACTIVE={'RECORDING','MOVING','BRAKING','SETTLING','RETURNING','STOPPING','POSTROLL'}
    def __init__(self,validation=None):
        self.validation=validation or Validation();self.state='READY';self.events=[]
        self.command=0.;self.owner=None;self.arm_token=None;self.arm_at=0.
        self.seen=set();self.heartbeat=-1.;self.last_seq=-1;self.last_tick=None
        self.run_id=None;self.reason='';self.segment='forward';self.stop_latched=False

    @property
    def active(self): return self.state in self.ACTIVE

    def event(self,name,now,**data): self.events.append({'mono':now,'event':name,'segment':self.segment,'run_id':self.run_id,**data})

    def arm(self,now,checks):
        required=('space_clear','physical_stop_ready','cup_secure','origin_checked','operator_present')
        if self.active or self.owner or any(checks.get(k) is not True for k in required):
            raise ValueError('Complete local safety checks; controller must be idle')
        self.stop_latched=False;self.arm_token=secrets.token_urlsafe(24);self.arm_at=now
        self.state='ARMED';return self.arm_token

    def start(self,c,run_id,token,now,obs):
        if run_id in self.seen: raise ValueError('Duplicate run rejected')
        if self.active or self.owner or token!=self.arm_token or not token or now-self.arm_at>30:
            raise ValueError('Fresh single-use ARM required')
        self.validation.check(c)
        self._precheck(obs,c)
        if c.tray!='OFF' and not obs.tray_ok:raise ValueError('Tray fault')
        abrupt=c.stop_semantics=='brake_at_distance'
        if abrupt and c.brake_offset_m:raise ValueError('brake_at_distance requires zero brake offset')
        profile=Profile(max(.01,c.distance-c.brake_offset_m),c.speed,c.acceleration,c.deceleration,c.jerk,c.s_curve,abrupt=abrupt,strength=c.s_curve_strength)
        possible_cruise=profile.cruise_duration
        if c.scenario=='planned_stop' and (profile.peak<c.speed-1e-6 or possible_cruise<c.min_cruise_s-1e-8):
            raise ValueError('Distance too short for target speed and >=1.5s cruise; choose a validated longer distance or profile')
        self.seen.add(run_id);self.arm_token=None;self.run_id=run_id;self.c=c
        self.events=[]
        self.owner='experiment';self.started=now;self.phase_at=now;self.heartbeat=now;self.last_seq=-1
        self.origin=(obs.x,obs.y,obs.yaw);self.return_origin=None;self.segment='forward'
        self.state='RECORDING';self.command=0.;self.last_tick=now;self.stop_since=None
        self.brake_x=None;self.end_x=None;self.forward_distance=None
        self.run_speed_limit=max(self.validation.max_speed,c.speed) if c.commissioning else self.validation.max_speed
        self.cruise_since=None;self.cruise_confirmed=False;self.speed_samples=[]
        self.motion_observed=False;self.unexpected_stop_since=None
        self.overspeed_since=None
        self.profile=profile
        if c.stop_semantics=='brake_at_distance':self.profile.brake_time=self.profile.acceleration_duration+possible_cruise
        self.event('RECORDING',now,run_id=run_id,config=asdict(c),origin=self.origin,effective_jerk=profile.effective_jerk,s_curve_strength=profile.strength)

    def beat(self,seq,now):
        if type(seq) is not int or seq<=self.last_seq: raise ValueError('Stale/duplicate heartbeat')
        self.last_seq=seq;self.heartbeat=now

    def _precheck(self,o,config=None):
        if not all(math.isfinite(v) for v in (o.x,o.y,o.yaw,o.v)):raise ValueError('Invalid odometry')
        c=config or getattr(self,'c',None)
        if not o.live:raise ValueError('Live chassis odometry lost')
        if not o.recorder_ok:raise ValueError('Recording failure')
        if c is None or c.obstacle_stop:
            if not o.sensors:raise ValueError('Fresh obstacle sensor observations unavailable')
            if not o.clear_front:raise ValueError('Front obstacle detected')
        if c is not None and c.tray!='OFF' and not o.tray_ok:raise ValueError('Tray fault')

    def abort(self,reason,now):
        self.command=0.;self.state='ABORTED';self.reason=reason;self.stop_latched=True
        self.owner=None;self.arm_token=None;self.event('ABORT',now,reason=reason)
        return 0.

    def along(self,o):
        x,y,yaw=self.origin
        return (o.x-x)*math.cos(yaw)+(o.y-y)*math.sin(yaw)

    def tick(self,now,o):
        if not self.active:return 0.
        try:
            self._precheck(o)
            heartbeat_timeout=2. if self.c.commissioning and self.c.record_priority else .5
            if self.state not in ('SETTLING','POSTROLL') and now-self.heartbeat>heartbeat_timeout:
                raise ValueError('Windows heartbeat timeout')
            if self.last_tick is not None and not 0<=now-self.last_tick<=.15:raise ValueError('Motor control tick delayed')
            if now-self.started>120:raise ValueError('Run duration exceeded')
            along=self.along(o)
            x,y,yaw=self.origin
            cross=-(o.x-x)*math.sin(yaw)+(o.y-y)*math.cos(yaw)
            if abs(cross)>.12 or abs(math.atan2(math.sin(o.yaw-yaw),math.cos(o.yaw-yaw)))>.20:
                raise ValueError('Path/yaw deviation')
            if along>self.c.distance+.12 or along<-.08:raise ValueError('Distance envelope exceeded')
            if abs(o.v)>self.run_speed_limit*1.5+.03:raise ValueError('Actual speed exceeded: hard limit')
            if abs(o.v)>self.run_speed_limit*1.15+.015:
                if self.overspeed_since is None:
                    self.overspeed_since=now;self.event('SPEED_TRANSIENT',now,measured_v=o.v)
                if now-self.overspeed_since>=.2:raise ValueError('Actual speed exceeded: sustained for 0.2s')
            else:self.overspeed_since=None
            if self.segment=='return' and (not o.clear_rear or not self.validation.reverse):raise ValueError('Rear clearance unavailable')
        except ValueError as e:return self.abort(str(e),now)
        self.last_tick=now;elapsed=now-self.phase_at
        if self.state=='RECORDING':
            if elapsed>=self.c.pre_roll:
                self.phase_at=now;self.state='MOVING';self.event('MOVING',now)
        elif self.state in ('MOVING','BRAKING','RETURNING'):
            d,v,a,j=self.profile.at(elapsed)
            abrupt=self.c.stop_semantics=='brake_at_distance' and self.segment=='forward'
            if abrupt and elapsed>=self.profile.acceleration_duration:v=self.c.speed;a=j=0.
            if self.segment=='forward' and self.state=='MOVING':
                if abs(o.v)>.015:self.motion_observed=True;self.unexpected_stop_since=None
                elif self.motion_observed and abs(o.v)<.005:
                    if self.unexpected_stop_since is None:self.unexpected_stop_since=now
                    if now-self.unexpected_stop_since>=.3:return self.abort('Intermediate actual stop before planned braking; trial invalid',now)
                else:self.unexpected_stop_since=None
            if self.segment=='forward' and (abrupt or elapsed<self.profile.brake_time):
                self.speed_samples.append((now,o.v));self.speed_samples=[sample for sample in self.speed_samples if now-sample[0]<=.3]
                mean_v=sum(v for t,v in self.speed_samples)/len(self.speed_samples)
                stable=len(self.speed_samples)>=4 and abs(mean_v-self.c.speed)<=max(.005,self.c.speed*.05)+1e-6 and max(abs(v-self.c.speed) for t,v in self.speed_samples)<=max(.015,self.c.speed*.15)+1e-6 and abs(a)<1e-8
                if stable:
                    if self.cruise_since is None:self.cruise_since=now
                    if not self.cruise_confirmed and now-self.cruise_since>=self.c.min_cruise_s:
                        self.cruise_confirmed=True;self.event('CRUISE_CONFIRMED',now,duration_s=now-self.cruise_since,measured_v=mean_v,instantaneous_v=o.v,filter_window_s=.3,max_sample_error_fraction=.15)
                elif not self.cruise_confirmed:self.cruise_since=None
            if self.segment=='return': remaining=along;sign=-1.
            else:remaining=self.c.distance-along;sign=1.
            self.command=sign*v
            if self.c.scenario=='injected_stop' and self.segment=='forward' and elapsed>=self.profile.brake_time:
                return self.abort('Injected obstacle safety stop',now)
            trigger=(remaining<=0.) if abrupt else elapsed>=self.profile.brake_time
            if trigger and self.brake_x is None:
                if self.segment=='forward' and not self.cruise_confirmed and self.cruise_since is not None and now-self.cruise_since>=self.c.min_cruise_s and abs(o.v-self.c.speed)<=max(.005,self.c.speed*.05):
                    self.cruise_confirmed=True;self.event('CRUISE_CONFIRMED',now,duration_s=now-self.cruise_since,measured_v=o.v)
                if self.segment=='forward' and self.c.scenario=='planned_stop' and not self.cruise_confirmed:
                    self.event('PROTOCOL_WARNING',now,reason='Actual >=1.5s constant-speed interval not verified; preserve complete stop observation',x=along)
                self.brake_x=along;self.event('BRAKE_REQUEST',now,x=along,v=o.v)
                if self.segment=='forward':self.state='BRAKING'
            if (abrupt and remaining<=0.) or (not abrupt and (remaining<=.01 or elapsed>=self.profile.duration)):
                self.command=0.;self.state='STOPPING';self.phase_at=now
                self.event('ZERO_COMMAND',now,x=along,v=o.v)
        elif self.state=='STOPPING':
            self.command=0.
            if abs(o.v)<.008:
                if self.stop_since is None:self.stop_since=now
                if now-self.stop_since>=.4:
                    self.end_x=along;self.event('STOP_CONFIRMED',now,x=along,brake_x=self.brake_x)
                    self.state='SETTLING' if self.segment=='forward' else 'POSTROLL';self.phase_at=now
                    if self.segment=='forward':self.forward_distance=max(0.,along)
            else:self.stop_since=None
            if elapsed>4.:return self.abort('Stop not confirmed',now)
        elif self.state=='SETTLING' and elapsed>=self.c.settle:
            if self.c.return_home and self.forward_distance>.02:
                if not o.clear_rear:return self.abort('Rear not clear',now)
                self.segment='return';self.brake_x=None;self.stop_since=None
                self.profile=Profile(self.forward_distance,self.c.speed,self.c.acceleration,self.c.deceleration,self.c.jerk,self.c.s_curve,strength=self.c.s_curve_strength)
                self.state='RETURNING';self.phase_at=now;self.event('RETURNING',now,distance=self.forward_distance)
            else:self.state='POSTROLL';self.phase_at=now
        elif self.state=='POSTROLL' and elapsed>=self.c.post_roll:
            self.state='COMPLETE';self.owner=None;self.command=0.;self.event('COMPLETE',now,x=along)
        return self.command


class TrayPredictor:
    """Bounded model command; never presented as measured servo response."""
    def __init__(self):self.angle=0.
    def step(self,c,profile,t,dt,active=True,brake_eta=None,measured_v=None):
        acceleration=profile.at(t+c.lead)[2] if active else 0.
        if active and c.stop_semantics=='brake_at_distance' and brake_eta is not None and brake_eta<=c.lead and (measured_v or 0)>.008:
            acceleration=-c.deceleration
        target=math.degrees(math.atan2(acceleration,9.81))*c.gain+c.center_deg if active else 0.
        target=max(-c.angle_limit,min(c.angle_limit,target))
        delta=(target-self.angle)*(1-math.exp(-dt/max(c.ramp,c.filter_s)))
        self.angle+=max(-c.angle_rate*dt,min(c.angle_rate*dt,delta))
        return self.angle
