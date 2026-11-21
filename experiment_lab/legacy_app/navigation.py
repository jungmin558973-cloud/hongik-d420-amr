"""Map-bound waypoints and the single-owner Nav2 command relay controller."""
from dataclasses import asdict
import json
import math
from pathlib import Path
import uuid
from engine import Config, Engine, Pose, clamp


def finite(value):return type(value) in (float,int) and math.isfinite(value)


def validate_point(point):
    if not isinstance(point,dict) or any(not finite(point.get(k)) for k in ('x','y','yaw')):
        raise ValueError('지도상의 위치와 방향을 확인해 주세요.')
    if abs(point['x'])>1000 or abs(point['y'])>1000 or abs(point['yaw'])>math.pi:
        raise ValueError('지점 좌표 범위를 확인해 주세요.')
    return {k:float(point[k]) for k in ('x','y','yaw')}


def check_map_point(grid,point):
    """Require the padded CAD body at the goal to fit observed free map cells."""
    point=validate_point(point)
    if grid is None:raise ValueError('최신 지도를 기다려 주세요.')
    info=grid.info;q=info.origin.orientation
    yaw=math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))
    c,s=math.cos(yaw),math.sin(yaw)
    ca,sa=math.cos(point['yaw']),math.sin(point['yaw'])
    step=min(.025,info.resolution/2)
    nx=math.ceil(.502/step);ny=math.ceil(.416/step)
    for i in range(nx+1):
        for j in range(ny+1):
            bx,by=-.220+.502*i/nx,-.208+.416*j/ny
            x=point['x']+ca*bx-sa*by-info.origin.position.x
            y=point['y']+sa*bx+ca*by-info.origin.position.y
            col=math.floor((c*x+s*y)/info.resolution)
            row=math.floor((-s*x+c*y)/info.resolution)
            if not 0<=col<info.width or not 0<=row<info.height:
                raise ValueError('차체가 지도 밖으로 나가는 위치입니다.')
            if not 0<=grid.data[row*info.width+col]<50:
                raise ValueError('차체 크기에 맞는 빈 공간을 선택해 주세요. 장애물·미측정 영역은 저장하지 않습니다.')
    return point


class Waypoints:
    def __init__(self,path):
        self.path=Path(path)
        self.maps=[]
        if self.path.exists():self.maps=json.loads(self.path.read_text())['maps']
        # A server/sensor restart requires a fresh save before binding this live map.
        self.current=None

    def write(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'version':1,'maps':self.maps},ensure_ascii=False,indent=2))
        temporary.replace(self.path)

    def bind(self,label,remote_path):
        label=str(label).strip()[:60] or '실험실 지도'
        item={'id':uuid.uuid4().hex,'name':label,'remote_path':remote_path,'points':[]}
        self.maps.append(item);self.current=item['id'];self.write();return item

    def active(self):return next((m for m in self.maps if m['id']==self.current),None)

    def add(self,name,point):
        item=self.active()
        if item is None:raise ValueError('먼저 현재 지도를 저장해 주세요.')
        point=validate_point(point)
        name=str(name).strip()[:40]
        if not name:raise ValueError('지점 이름을 입력해 주세요.')
        if any(p['name']==name for p in item['points']):raise ValueError('이미 있는 지점 이름입니다.')
        point.update(id=uuid.uuid4().hex,name=name)
        item['points'].append(point);self.write();return point

    def get(self,point_id):
        item=self.active()
        point=next((p for p in item['points'] if p['id']==point_id),None) if item else None
        if point is None:raise ValueError('현재 지도에 저장한 지점을 선택해 주세요.')
        return point


class Navigation(Engine):
    labels={**Engine.labels,'navigation':'지점으로 이동 중','nav_wait':'경로 명령 수신 대기'}

    def arm(self,now,pose,goal_id,speed=.40):
        config=Config.parse({'speed':speed},max_speed=1.5)
        super().start('brake','real',config,now,pose)
        self.kind='navigation';self.state='nav_wait';self.goal_id=goal_id
        self.nav_data={};self.wait_started=now
        self.message='Nav2의 새 이동 명령을 기다립니다.'

    def observe(self,data):self.nav_data=data

    def step(self,now,pose):
        if not self.active:return self.command
        if (not all(finite(v) for v in asdict(pose).values()) or abs(pose.v)>max(.25,self.config.speed*1.1) or abs(pose.w)>.5):
            self.stop('차체 피드백을 확인해 주세요.');return self.command
        data=self.nav_data
        if data.get('goal_id')!=self.goal_id:
            self.stop('이동 요청이 바뀌었습니다.');return self.command
        phase=data.get('phase')
        if phase=='succeeded':
            self.stop('저장한 지점에 도착했습니다.');self.state='done';return self.command
        if phase in ('failed','canceled','canceling'):
            self.stop(data.get('message') or '지점 이동이 종료됐습니다.');return self.command
        command=data.get('command',(0.,0.));received=data.get('command_at',0.)
        if not finite(received) or not 0<=now-received<=.25:
            self.command=(0.,0.);self.state='nav_wait'
            if now-self.wait_started>2.:self.stop('새 내비게이션 속도 명령이 없습니다.')
            return self.command
        if (phase!='moving' or not isinstance(command,(list,tuple)) or len(command)!=2 or
                not all(finite(v) for v in command) or not 0<=command[0]<=self.config.speed+.000001 or abs(command[1])>5.4):
            self.stop('내비게이션 속도 명령 범위를 벗어났습니다.');return self.command
        self.wait_started=now
        # Preserve path curvature by reducing both axes for a tight turn.
        # Clipping angular velocity alone would widen a fast corner.
        scale=min(1.,.2/abs(command[1])) if command[1] else 1.
        self.command=(command[0]*scale,command[1]*scale)
        self.state='navigation';self.message='저장한 지점으로 이동합니다.'
        self.pose=Pose(**asdict(pose));self.last_tick=now
        self.history.append({'t':round(now-self.started,3),**asdict(pose),
                             'cmd_v':self.command[0],'cmd_w':self.command[1],'phase':self.state})
        return self.command
