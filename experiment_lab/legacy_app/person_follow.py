"""Bidirectional close person follower using dated YOLO/depth observations."""
from dataclasses import asdict
import math
from engine import Engine, Config, Pose, clamp, wrap


def valid_observation(data, wall_now):
    if not isinstance(data,dict) or data.get('version')!=1 or data.get('ready') is not True:
        raise ValueError(data.get('reason','사람 검출 수신 대기') if isinstance(data,dict) else '사람 검출 수신 대기')
    for key,lo,hi in (('source_unix',wall_now-.4,wall_now+.1),
                      ('published_unix',wall_now-.4,wall_now+.1),
                      ('distance_m',.20,5.),('bearing_rad',-.8,.8)):
        value=data.get(key)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not lo<=value<=hi:
            raise ValueError('사람 거리·방향 정보가 없거나 지연됐습니다.')
    people=data.get('people')
    if not isinstance(data.get('target_id'),str) or len(data['target_id'])!=32 or not isinstance(people,list) or not people:
        raise ValueError('추종할 한 사람을 확인해 주세요.')
    if 'selection_valid' in data or len(people)>1:
        members,box=data.get('selected_indices'),data.get('selected_box')
        if (data.get('selection_valid') is not True or not isinstance(members,list) or not members
                or any(type(i) is not int or not 0<=i<len(people) for i in members)
                or len(set(members))!=len(members) or not isinstance(box,list) or len(box)!=4
                or not all(type(v) in (float,int) and math.isfinite(v) for v in box)
                or box[2]<=0 or box[3]<=0):
            raise ValueError('선택한 대상의 위치를 재확인하고 있습니다.')
    return data


class Follow(Engine):
    labels={**Engine.labels,'follow':'사람 따라가기','follow_wait':'일시 정지 · 재확인 중','follow_hold':'거리 유지 · 추종 대기'}
    WAIT_TIMEOUT=3.0
    RECOVERY_DWELL=.15

    def arm(self,config,now,pose,observation,wall_now,target_id,distance):
        if isinstance(distance,bool) or not isinstance(distance,(int,float)) or not math.isfinite(distance) or not .3<=distance<=2.0:
            raise ValueError('추종 거리는 0.3~2m로 설정해 주세요.')
        valid_observation(observation,wall_now)
        if target_id!=observation['target_id']:
            raise ValueError('추종 대상이 바뀌었습니다. 다시 확인해 주세요.')
        stop_distance=.20
        if observation['distance_m']<stop_distance+.05:
            raise ValueError(f'카메라에서 {stop_distance+.05:.2f}m 이상 떨어져 시작해 주세요.')
        # Keep existing stationary-start validation and all outer Lab watchdogs.
        super().start('brake','real',config,now,pose)
        self.kind=self.state='follow'
        self.target_id=target_id
        self.follow_distance=distance
        self.close_stop_distance=stop_distance
        self.observation=observation
        self.wall_now=wall_now
        self.wait_since=self.recover_since=None
        self.recover_frames=0
        self.recover_source=None
        self.wait_kind=self.wait_reason=''
        self.close_latched=False
        self.last_linear_sign=0
        self.message='같은 사람을 따라갑니다. 잠깐 놓치면 정지 후 재확인합니다.'

    def observe(self,data,wall_now):
        self.observation,self.wall_now=data,wall_now

    def terminal_observation_error(self,data):
        if not isinstance(data,dict) or data.get('version')!=1:
            return '검출 메시지가 올바르지 않습니다. 다시 시작해 주세요.'
        if data.get('fault')=='duplicate_publishers':
            return '검출 발행자가 중복됐습니다. 다시 연결해 주세요.'
        people=data.get('people')
        if not isinstance(people,list):return '사람 검출 형식이 올바르지 않습니다.'
        target=data.get('target_id')
        if target is not None and target!=self.target_id:
            return '추종 대상의 위치·외형이 달라졌습니다. 다시 선택해 주세요.'
        for key in ('source_unix','published_unix'):
            value=data.get(key)
            if (isinstance(value,bool) or not isinstance(value,(int,float))
                    or not math.isfinite(value) or value>self.wall_now+.1):
                return '검출 시각이 올바르지 않습니다. 다시 연결해 주세요.'
        for key in ('distance_m','bearing_rad'):
            value=data.get(key)
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value)):
                return '유효하지 않은 사람 거리·방향입니다.'
        return ''

    def wait(self,now,reason,kind='observation'):
        if self.state!='follow_wait' or self.wait_kind!=kind:
            self.wait_since=now
        self.state='follow_wait'
        self.wait_kind,self.wait_reason=kind,reason
        self.recover_since=self.recover_source=None
        self.recover_frames=0
        self.command=(0.,0.)
        self.message='일시 정지 · '+reason+' · 같은 대상 확인 후 자동 재개'
        if kind!='close' and now-self.wait_since>=self.WAIT_TIMEOUT:
            self.stop('대상·거리 확인이 3초 이상 끊겼습니다. 다시 시작해 주세요.')

    def drive_step(self,now,pose,previous,dt):
        if not all(math.isfinite(v) for v in asdict(pose).values()):
            self.stop('유효한 오도메트리가 없습니다.');return
        if abs(pose.v)>.20 or abs(pose.w)>.50:
            self.stop('추종 속도 범위를 초과했습니다.');return
        if math.hypot(pose.x-previous.x,pose.y-previous.y)>.10 or abs(wrap(pose.yaw-previous.yaw))>.25:
            self.stop('위치가 불연속적으로 변했습니다.');return
        problem=self.terminal_observation_error(self.observation)
        if problem:self.stop(problem);return
        try:data=valid_observation(self.observation,self.wall_now)
        except ValueError as exc:
            self.wait(now,str(exc));return
        distance,bearing=data['distance_m'],data['bearing_rad']
        if distance<.20:
            self.wait(now,'너무 가까워 유효 깊이를 확인할 수 없습니다.');return
        if self.state=='follow_wait':
            if self.wait_kind!='close' and now-self.wait_since>=self.WAIT_TIMEOUT:
                self.stop('대상·거리 확인이 3초 이상 끊겼습니다. 다시 시작해 주세요.');return
            source=data['source_unix']
            if self.recover_source is None or source>self.recover_source:
                if self.recover_since is None:self.recover_since=now
                self.recover_source=source;self.recover_frames+=1
            if self.recover_since is None or now-self.recover_since<self.RECOVERY_DWELL or self.recover_frames<3:
                self.command=(0.,0.)
                self.message='같은 대상·최신 거리 재확인 중 · 확인 후 자동 재개'
                return
            self.wait_since=self.recover_since=None
            self.wait_kind=self.wait_reason=''
        error=distance-self.follow_distance
        deadband=.02
        signed_error=math.copysign(max(0.,abs(error)-deadband),error)
        speed_limit=min(self.config.speed,.12)
        if distance<.55:speed_limit=min(speed_limit,.06)
        if distance<.40:speed_limit=min(speed_limit,.025)
        target_v=clamp(.7*signed_error,-min(.06,self.config.speed),speed_limit)
        target_w=clamp(1.2*bearing,-.30,.30) if abs(bearing)>.04 else 0.
        if target_v>=0:
            target_v*=clamp((.5-abs(bearing))/.3,0.,1.)
        else:
            target_w=0. # Back away straight while keeping the front camera on the target.
        sign=1 if target_v>0 else -1 if target_v<0 else 0
        current=self.command[0]
        reversing=bool(sign and self.last_linear_sign and sign!=self.last_linear_sign)
        if reversing and (abs(pose.v)>.025 or abs(current)>1e-6):
            v=0.
        elif sign<0 and abs(pose.w)>.08:v=0.
        elif not sign:v=0.
        elif sign>0:v=min(target_v,max(0.,current)+.25*dt)
        else:v=max(target_v,min(0.,current)-.15*dt)
        if abs(v)>1e-6:self.last_linear_sign=1 if v>0 else -1
        w=self.command[1]+clamp(target_w-self.command[1],-.9*dt,.9*dt) if target_w else 0.
        self.command=(v,w)
        self.state='follow_hold' if not v and not w else 'follow'
        self.message=('거리 유지 중 · 사람이 멀어지면 자동으로 따라갑니다.' if self.state=='follow_hold'
                      else '가까워져 후진하며 간격을 유지합니다.' if v<0 else '선택한 사람을 따라갑니다.')

    def step(self,now,pose):
        if not self.active:return self.command
        previous=self.pose
        dt=clamp(now-self.last_tick,.001,.1);self.last_tick=now
        self.drive_step(now,pose,previous,dt)
        # Keep serialized history finite even if malformed odometry caused a stop.
        if all(math.isfinite(v) for v in asdict(pose).values()):self.pose=Pose(**asdict(pose))
        pose=self.pose
        self.distance+=math.hypot(pose.x-previous.x,pose.y-previous.y)
        self.peak_speed=max(self.peak_speed,abs(pose.v))
        self.history.append({'t':round(now-self.started,3),'x':pose.x,'y':pose.y,'yaw':pose.yaw,
                             'v':pose.v,'w':pose.w,'cmd_v':self.command[0],'cmd_w':self.command[1],'phase':self.state})
        if len(self.history)>12000:self.stop('추종 세션 기록 한도에 도달했습니다. 저장 후 다시 시작해 주세요.')
        return self.command

    def snapshot(self,now):
        return {**super().snapshot(now),'follow_target_id':getattr(self,'target_id',None),
                'follow_distance':getattr(self,'follow_distance',.3),
                'follow_waiting':self.active and self.state=='follow_wait',
                'follow_wait_reason':getattr(self,'wait_reason',''),
                'follow_wait_remaining_s':max(0.,self.WAIT_TIMEOUT-(now-self.wait_since))
                    if self.active and self.state=='follow_wait' and self.wait_kind!='close' else None}
