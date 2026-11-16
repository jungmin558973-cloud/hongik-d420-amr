"""Odometry L-route with fresh front depth checks; no map/Nav2/lidar commands."""
from dataclasses import asdict
import math
from engine import Engine,Config,Pose,clamp,wrap


class DepthFault(ValueError):
    def __init__(self,message,code,recoverable=False):
        super().__init__(message);self.code=code;self.recoverable=recoverable


def valid_depth(data,wall_now):
    if not isinstance(data,dict) or data.get('version')!=1:
        raise DepthFault('깊이 관측 형식 오류','invalid_format')
    if data.get('ready') is not True:
        fault=data.get('fault','not_ready')
        raise DepthFault(data.get('reason','깊이 카메라 관측 대기'),fault,
                         fault not in ('duplicate_publishers','invalid_format','calibration'))
    for k in ('source_unix','published_unix'):
        v=data.get(k)
        if type(v) not in (int,float) or not math.isfinite(v) or wall_now-v<-.1:
            raise DepthFault('깊이 관측 시각/장치 시각 동기화를 확인해 주세요.','invalid_time')
        if wall_now-v>.35:
            raise DepthFault('깊이 카메라 관측이 지연됐습니다.','stale_'+k,True)
    pts=data.get('points')
    if not isinstance(pts,list) or len(pts)>5000 or any(not isinstance(p,list) or len(p)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in p) for p in pts):
        raise DepthFault('깊이 장애물 좌표 오류','invalid_points')
    return pts


def clear(points,v,w):
    # Observed points only: the front camera cannot certify side/rear clearance.
    margin=.08+v*v/(2*.25)
    # Bound includes the entire swept body plus braking padding at 0.40m/s.
    extent=abs(v)+.502+margin
    points=[p for p in points if abs(p[0])<=extent and abs(p[1])<=extent]
    for i in range(11):
        t=i*.1;a=w*t
        x,y=(v/w*math.sin(a),v/w*(1-math.cos(a))) if abs(w)>1e-6 else (v*t,0.)
        c,s=math.cos(a),math.sin(a)
        for px,py in points:
            dx,dy=px-x,py-y;bx,by=c*dx+s*dy,-s*dx+c*dy
            if -.220-margin<=bx<=.282+margin and abs(by)<=.208+margin:return False
    return True


class DepthRoute(Engine):
    labels={**Engine.labels,'depth_first':'첫 직선','depth_turn':'오른쪽 90° 회전','depth_second':'두 번째 직선',
            'depth_brake':'중간 급정지 시험','depth_turn_settle':'회전 전 정지 확인','depth_finish':'도착 정지 확인'}
    MAX_SPEED=.20
    RECOVERY_TIMEOUT=3.0
    RECOVERY_FRAMES=3
    RECOVERY_DWELL=.15

    def arm(self,first,second,speed,now,pose,data,wall_now,brake=True):
        for v,lo,hi in [(first,.2,10.),(second,.2,10.),(speed,.03,self.MAX_SPEED)]:
            if type(v) not in (float,int) or not math.isfinite(v) or not lo<=v<=hi:raise ValueError('거리 0.2~10m, 속도 3~20cm/s로 설정해 주세요.')
        if type(brake) is not bool:raise ValueError('급정지 시험 설정은 true/false여야 합니다.')
        valid_depth(data,wall_now)
        super().start('brake','real',Config(speed=speed),now,pose)
        self.kind='depth-route';self.state='depth_first';self.lengths=(first,second)
        self.target_yaw=pose.yaw;self.segment_origin=Pose(**asdict(pose));self.observation=data;self.wall_now=wall_now
        self.observed_at=now;self.wait_since=None;self.turn_good_since=None;self.best_remaining=first
        self.pause_at=None;self.pause_reason='';self.pause_code='';self.pause_count=0
        self.recovery_count=0;self.recovery_source=None;self.recovery_first_at=None
        self.depth_diagnostics={}
        self.brake_enabled=brake;self.brake_done=False;self.brake_at=first/2
        self.brake_trigger_distance=None;self.brake_end_distance=None
        self.hold_started=None;self.stationary_since=None;self.events=[]
        self.route_timeout=max(240.,3*(first+second)/speed+60.)
        self.message='전방 깊이 관측 기반 · 첫 직선'

    def enter_hold(self,state,now):
        self.state=state;self.command=(0.,0.);self.hold_started=now;self.stationary_since=None
        self.wait_since=None;self.events.append({'t':now-self.started,'event':state,'distance':self.distance})

    def hold(self,now,pose):
        self.command=(0.,0.);self.message=self.labels[self.state]
        if now-self.hold_started>8:
            self.stop('정지 확인 시간 초과 · 차체 제동/오도메트리를 확인하세요.');return
        if abs(pose.v)<.015 and abs(pose.w)<.04:
            if self.stationary_since is None:self.stationary_since=now
        else:self.stationary_since=None
        dwell=1.0 if self.state=='depth_brake' else .4
        if self.stationary_since is None or now-self.stationary_since<dwell:return
        prior=self.state
        self.events.append({'t':now-self.started,'event':prior+'_confirmed','distance':self.distance})
        self.last_progress_at=now;self.wait_since=None
        if prior=='depth_brake':
            self.brake_done=True;self.brake_end_distance=self.distance;self.state='depth_first'
            self.message='급정지 확인 완료 · 첫 직선 재출발'
        elif prior=='depth_turn_settle':
            self.state='depth_turn';self.target_yaw=wrap(self.origin.yaw-math.pi/2)
        else:
            self.stop('L자 경로 완료 · 실제 정지 확인');self.state='done'

    def observe(self,data,wall_now):self.observation,self.wall_now=data,wall_now

    def pause(self,reason,now,code='control_delay'):
        self.command=(0.,0.);self.turn_good_since=None;self.stationary_since=None
        if self.pause_at is None:self.pause_at=now;self.pause_count+=1
        self.pause_reason,self.pause_code=reason,code
        self.recovery_count=0;self.recovery_source=None;self.recovery_first_at=None
        self.message='일시 정지 · '+reason+' · 새 깊이 영상 확인 중'
        if now-self.pause_at>=self.RECOVERY_TIMEOUT:
            self.stop('관측 복구 시간 초과 · '+reason)

    def sample(self,now,pose):
        self.history.append({'t':now-self.started,**asdict(pose),'cmd_v':self.command[0],
            'cmd_w':self.command[1],'phase':self.state,'depth_paused':self.pause_at is not None,
            'depth_fault':self.pause_code,'depth_diagnostics':dict(self.depth_diagnostics)})
        if len(self.history)>20000:del self.history[:-20000]

    def snapshot(self,now):
        return {**super().snapshot(now),'route_lengths':self.lengths,
            'depth_paused':self.active and self.pause_at is not None,'depth_pause_count':self.pause_count,
            'depth_recovery_frames':self.recovery_count,'depth_diagnostics':self.depth_diagnostics,
            'brake_enabled':self.brake_enabled,'brake_done':self.brake_done,'brake_at_m':self.brake_at,
            'brake_trigger_distance':self.brake_trigger_distance,'brake_end_distance':self.brake_end_distance,
            'events':list(self.events)}

    def step(self,now,pose):
        if not self.active:return self.command
        old=self.pose;previous_command=self.command;dt=clamp(now-self.last_tick,.001,.1);self.last_tick=now;self.pose=Pose(**asdict(pose))
        self.command=(0.,0.)
        data=self.observation if isinstance(self.observation,dict) else {}
        self.depth_diagnostics={k:data.get(k) for k in ('processing_ms','frame_age_at_publish_ms',
            'receive_source_age_ms','receive_publish_age_ms','receive_gap_ms','transport_age_ms','sequence','observer_session')}
        for key in ('source_unix','published_unix'):
            value=data.get(key)
            if type(value) in (int,float) and math.isfinite(value):
                self.depth_diagnostics[key+'_age_ms']=round((self.wall_now-value)*1000,2)
        if (not all(math.isfinite(v) for v in asdict(pose).values()) or abs(pose.v)>max(.25,self.config.speed*1.25) or abs(pose.w)>.65 or
                math.hypot(pose.x-old.x,pose.y-old.y)>.12 or abs(wrap(pose.yaw-old.yaw))>.25):
            self.stop('주행 오도메트리를 확인해 주세요.');return self.command
        self.distance+=math.hypot(pose.x-old.x,pose.y-old.y)
        self.peak_speed=max(self.peak_speed,abs(pose.v))
        self.peak_acceleration=max(self.peak_acceleration,abs(pose.v-old.v)/dt)
        try:points=valid_depth(self.observation,self.wall_now)
        except DepthFault as exc:
            self.stationary_since=None
            if exc.recoverable:self.pause(str(exc),now,exc.code)
            else:self.pause_code=exc.code;self.stop(str(exc))
            self.sample(now,pose);return self.command
        if self.pause_at is not None:
            if now-self.pause_at>=self.RECOVERY_TIMEOUT:
                self.stop('관측 복구 시간 초과 · '+self.pause_reason)
                self.sample(now,pose);return self.command
            key=(data.get('observer_session'),data['source_unix'])
            # Require fresh distinct frames, a stationary chassis, and timing headroom.
            if (self.wall_now-data['source_unix']>.25 or abs(pose.v)>.015 or abs(pose.w)>.04):
                self.recovery_count=0;self.recovery_source=None;self.recovery_first_at=None
            elif self.recovery_source is not None and key[0]!=self.recovery_source[0]:
                self.recovery_source=key;self.recovery_count=1;self.recovery_first_at=now
            elif self.recovery_source is None or key[1]>self.recovery_source[1]:
                if self.recovery_first_at is None:self.recovery_first_at=now
                self.recovery_source=key;self.recovery_count+=1
            if (self.recovery_count<self.RECOVERY_FRAMES or self.recovery_first_at is None or
                    now-self.recovery_first_at<self.RECOVERY_DWELL):
                self.sample(now,pose);return self.command
            duration=now-self.pause_at;self.last_progress_at+=duration
            if self.wait_since is not None:self.wait_since+=duration
            self.pause_at=None;self.pause_code='';self.message='깊이 관측 복구 · 저속으로 경로 재개'
        if now-self.started>self.route_timeout:
            self.stop('경로 실행 시간이 초과됐습니다.');return self.command
        if self.state in ('depth_brake','depth_turn_settle','depth_finish'):
            self.hold(now,pose);self.sample(now,pose);return self.command
        if self.state=='depth_turn':
            error=wrap(self.target_yaw-pose.yaw)
            if abs(error)<.04 and abs(pose.w)<.06:
                if self.turn_good_since is None:self.turn_good_since=now
                if now-self.turn_good_since>=.3:
                    self.state='depth_second';self.segment_origin=Pose(**asdict(pose));self.best_remaining=self.lengths[1];self.last_progress_at=now
            else:
                self.turn_good_since=None
                w=clamp(1.2*error,-.2,.2)
                if clear(points,0,w):self.command=(0.,w)
            self.message='오른쪽 90° 회전 · 전방만 관측, 측후방은 미관측'
        else:
            dx,dy=pose.x-self.segment_origin.x,pose.y-self.segment_origin.y
            c,s=math.cos(self.target_yaw),math.sin(self.target_yaw)
            along=c*dx+s*dy;cross=-s*dx+c*dy
            length=self.lengths[self.state=='depth_second'];remaining=length-along
            if abs(cross)>.25:
                self.stop('지정 경로에서 25cm 이상 벗어났습니다.');return self.command
            if self.state=='depth_first' and self.brake_enabled and not self.brake_done and along>=self.brake_at:
                self.brake_trigger_distance=self.distance;self.enter_hold('depth_brake',now)
                self.message='첫 직선 중간 · 0 속도 급정지 명령';self.sample(now,pose);return self.command
            if remaining<.03:
                if self.state=='depth_first':
                    self.enter_hold('depth_turn_settle',now)
                else:
                    self.enter_hold('depth_finish',now)
            else:
                desired=clamp(1.5*wrap(self.target_yaw-pose.yaw)-1.2*cross,-.25,.25)
                limit=min(self.config.speed,max(.025,.5*remaining))
                candidates=[]
                for w in (desired,clamp(desired+.15,-.25,.25),clamp(desired-.15,-.25,.25)):
                    for v in (limit,limit*.5):
                        if clear(points,v,w):
                            # Prefer course tracking; only small observed-space deviations.
                            score=v-.10*abs(w-desired)-.05*abs(cross+v*math.sin(w))
                            candidates.append((score,v,w))
                if candidates:
                    _,v,w=max(candidates);self.command=(min(v,max(0,previous_command[0])+.15*dt),w)
                self.message=('첫 직선' if self.state=='depth_first' else '회전 후 직선')+f' · 남은 거리 {max(0,remaining):.2f}m'
                if remaining<self.best_remaining-.02:self.best_remaining=remaining;self.last_progress_at=now
        if self.active and self.command==(0.,0.):
            if self.wait_since is None:self.wait_since=now
            if self.state!='depth_second':self.message+=' · 전방 관측 확인/장애물 대기'
            if now-self.wait_since>15:self.stop('장애물 또는 회전 관측 대기가 길어졌습니다.')
        else:self.wait_since=None
        if self.active and now-self.last_progress_at>35:self.stop('진행이 확인되지 않습니다.')
        self.sample(now,pose)
        return self.command
