"""Causal L-route feedforward. No future IMU, depth, or serial I/O."""
from dataclasses import dataclass,asdict
import math
from engine import clamp

@dataclass
class RoutePredictionConfig:
    lead: float=.20
    angle_limit: float=2.0
    brake_seconds: float=.20
    roll_sign: int=1
    pitch_sign: int=1
    swap_axes: bool=False
    @classmethod
    def parse(cls,data):
        if not isinstance(data,dict) or set(data)-set(cls.__dataclass_fields__):raise ValueError('L자 예측 설정 항목 오류')
        c=cls(**data)
        for value,lo,hi in [(c.lead,.05,.5),(c.angle_limit,.5,3.),(c.brake_seconds,.1,.6)]:
            if type(value) not in (int,float) or not math.isfinite(value) or not lo<=value<=hi:raise ValueError('L자 예측 시간·각도·제동 모델 범위를 확인하세요.')
        if type(c.roll_sign) is not int or c.roll_sign not in (-1,1) or type(c.pitch_sign) is not int or c.pitch_sign not in (-1,1) or type(c.swap_axes) is not bool:raise ValueError('예측 축/부호 설정 오류')
        return c

class RoutePredictor:
    def __init__(self,config=None):
        self.config=config or RoutePredictionConfig();self.roll=self.pitch=0.;self.previous_v=0.
        self.last_at=None;self.diagnostics={}
    def step(self,engine,pose,now):
        c=self.config;dt=.05 if self.last_at is None else clamp(now-self.last_at,.001,.1);self.last_at=now
        ax=0.;eta=None;source='LEVEL';v,w=engine.command
        healthy=engine.active and engine.pause_at is None
        if healthy:
            # Current command slew and odometry speed; this is NOT measured IMU acceleration.
            ax=clamp((v-self.previous_v)/dt,-1.5,1.5)
            if engine.state=='depth_smooth_stop' and engine.smooth_stop:
                ax=engine.smooth_stop.acceleration(now+c.lead);source='SCURVE_ACCELERATION_PREVIEW'
            if engine.state=='depth_first' and engine.brake_enabled and not engine.brake_done:
                dx=pose.x-engine.segment_origin.x;dy=pose.y-engine.segment_origin.y
                along=dx*math.cos(engine.target_yaw)+dy*math.sin(engine.target_yaw)
                remaining=engine.brake_at-along
                speed=max(0.,pose.v)
                if speed>.02 and remaining>=0:
                    eta=remaining/speed
                    if eta<=c.lead:
                        ax=-speed/c.brake_seconds;source='PLANNED_BRAKE_PREVIEW'
            elif engine.state=='depth_brake':
                ax=-max(0.,pose.v)/c.brake_seconds if pose.v>.015 else 0.
                source='BRAKE_RESPONSE' if ax else 'RETURN_LEVEL'
            if source=='LEVEL' and abs(ax)>.01:source='COMMAND_ACCELERATION'
        else:
            v=w=0.;source='PAUSED_LEVEL' if engine.active else 'END_LEVEL'
        self.previous_v=v
        r=-math.degrees(math.atan2(v*w,9.81)) if healthy else 0.
        p=math.degrees(math.atan2(ax,9.81)) if healthy else 0.
        if c.swap_axes:r,p=p,r
        r=clamp(r*c.roll_sign,-c.angle_limit,c.angle_limit);p=clamp(p*c.pitch_sign,-c.angle_limit,c.angle_limit)
        alpha=1-math.exp(-dt/.06);rate=12*dt
        self.roll=clamp(self.roll+clamp(alpha*(r-self.roll),-rate,rate),-c.angle_limit,c.angle_limit)
        self.pitch=clamp(self.pitch+clamp(alpha*(p-self.pitch),-rate,rate),-c.angle_limit,c.angle_limit)
        self.diagnostics=dict(source=source,brake_eta_s=eta,model_acceleration_m_s2=ax,
                              roll_deg=self.roll,pitch_deg=self.pitch,config=asdict(c),
                              vehicle_imu_used=False)
        return self.roll,self.pitch
