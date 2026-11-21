"""Operator teleop and causal command/course prediction. No hardware I/O."""
from dataclasses import dataclass, asdict
import copy
import math
import secrets

from engine import Engine, Pose, clamp, wrap


class Teleop(Engine):
    labels = {**Engine.labels, 'teleop': '수동 조종 · 키를 누르는 동안 이동'}
    INPUT_TIMEOUT = .30
    MAX_SPEED = 1.50
    MAX_YAW_SPEED = 5.4  # Firmware command ceiling; not a measured chassis speed.
    ACCELERATION = .75  # m/s²: 0.30m/s in 0.4s; full catalogue speed in 2s.
    ANGULAR_ACCELERATION = 4.0

    def __init__(self):
        super().__init__()
        self.axes = (0, 0)
        self.yaw_speed = .20
        self.input_at = 0.
        self.sequence = -1
        self.control_id = ''
        self.neutral_required = False

    def hold(self, reason):
        """Brake without discarding ownership; never resume an old held command."""
        self.command = (0., 0.)
        self.axes = (0, 0)
        self.neutral_required = True
        self.message = reason + ' · 키를 모두 놓았다 다시 눌러 주세요.'

    def arm(self, mode, config, now, pose):
        super().start('brake', mode, config, now, pose)
        self.kind = 'teleop'
        self.state = 'teleop'
        self.message = 'WASD/방향키 또는 화면 버튼을 누르는 동안 이동합니다. 키를 놓으면 정지합니다.'
        self.control_id = secrets.token_urlsafe(16)
        self.input_at = now

    def input(self, forward, turn, speed, yaw_speed, sequence, control_id, now):
        if not self.active or control_id != self.control_id:
            raise ValueError('수동 조종을 다시 켜 주세요.')
        if any(isinstance(x, bool) or x not in (-1, 0, 1) for x in (forward, turn)):
            raise ValueError('수동 방향 입력을 확인해 주세요.')
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= self.sequence:
            raise ValueError('이전 수동 입력을 거절했습니다.')
        for value, low, high in ((speed, .03, self.MAX_SPEED), (yaw_speed, .05, self.MAX_YAW_SPEED)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError('직진 3~150 cm/s, 회전 0.05~5.40 rad/s 범위로 설정해 주세요.')
        if self.neutral_required:
            if forward or turn:
                forward = turn = 0
            else:
                self.neutral_required = False
                self.message = '조종 준비됨 · WASD/방향키로 이동합니다.'
        self.axes = (forward, turn)
        self.config.speed, self.yaw_speed = speed, yaw_speed
        self.sequence, self.input_at = sequence, now
        # Releasing either axis stops that axis immediately, including reversal.
        self.command = (self.command[0] if forward else 0., self.command[1] if turn else 0.)

    def step(self, now, pose):
        previous = self.pose
        self.pose = Pose(**asdict(pose))
        dt = clamp(now - self.last_tick, .001, .1)
        self.last_tick = now
        if not self.active:
            return self.command
        if not all(math.isfinite(v) for v in asdict(pose).values()):
            self.stop('유효한 오도메트리를 기다려 주세요.')
        elif now - self.input_at > self.INPUT_TIMEOUT:
            self.hold('수동 입력이 지연됐습니다.')
        elif math.hypot(pose.x-previous.x, pose.y-previous.y) > max(.10,self.MAX_SPEED*1.1*dt+.03) or abs(wrap(pose.yaw-previous.yaw)) > max(.25,self.MAX_YAW_SPEED*1.2*dt+.05):
            self.stop('위치가 불연속적으로 변했습니다.')
        elif abs(pose.v) > self.MAX_SPEED*1.1 or abs(pose.w) > self.MAX_YAW_SPEED*1.2:
            self.stop('수동 시험 속도 범위를 초과했습니다.')
        if self.active:
            forward, turn = self.axes
            v, w = self.command
            v += clamp(forward*self.config.speed-v, -self.ACCELERATION*dt, self.ACCELERATION*dt)
            w += clamp(turn*self.yaw_speed-w, -self.ANGULAR_ACCELERATION*dt, self.ANGULAR_ACCELERATION*dt)
            # Keep the outside wheel within the linear envelope on a fast arc.
            # Use the complete 396mm chassis width as a conservative track bound.
            scale=min(1.,self.MAX_SPEED/(abs(v)+.198*abs(w))) if v or w else 1.
            v,w=v*scale,w*scale
            self.command = (v if forward else 0., w if turn else 0.)
        self.distance += math.hypot(pose.x-previous.x, pose.y-previous.y)
        self.peak_speed = max(self.peak_speed, abs(pose.v))
        self.peak_acceleration = max(self.peak_acceleration, abs(pose.v-previous.v)/dt)
        self.history.append({'t': round(now-self.started, 3), 'x':pose.x, 'y':pose.y, 'yaw':pose.yaw,
                             'v':pose.v, 'w':pose.w, 'cmd_v':self.command[0], 'cmd_w':self.command[1], 'phase':self.state})
        # Bound memory for an extended manual session; elapsed stays absolute.
        if len(self.history) > 12000:
            del self.history[:len(self.history)-12000]
        return self.command

    def snapshot(self, now):
        return {**super().snapshot(now), 'control_id':self.control_id, 'axes':self.axes,
                'teleop_speed':self.config.speed, 'teleop_yaw_speed':self.yaw_speed,
                'neutral_required':self.neutral_required,
                'input_age_s':max(0, now-self.input_at) if self.input_at else None}


@dataclass
class PredictionConfig:
    lead: float = .20
    angle_limit: float = 5.
    roll_sign: int = 1
    pitch_sign: int = 1
    swap_axes: bool = False

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or set(data)-set(cls.__dataclass_fields__):
            raise ValueError('예측 설정 항목을 확인해 주세요.')
        c = cls(**data)
        for value, lo, hi in ((c.lead, .05, .5), (c.angle_limit, .5, 8.)):
            if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or not lo <= value <= hi:
                raise ValueError('선행시간 0.05~0.5초, 예측각 제한 0.5~8°로 설정해 주세요.')
        if any(isinstance(x,bool) or x not in (-1,1) for x in (c.roll_sign,c.pitch_sign)) or not isinstance(c.swap_axes,bool):
            raise ValueError('예측 축과 부호를 확인해 주세요.')
        return c


class Predictor:
    """Only command plans/current pose and entered geometry; never future sensors."""
    def __init__(self):
        self.roll = self.pitch = 0.

    def reset(self):
        self.roll = self.pitch = 0.

    @staticmethod
    def model_body_pitch(x, config):
        def height(px):
            f=(px-config.bump_gap)/config.bump_width
            return config.bump_height*(1-abs(2*f-1)) if 0<f<1 else 0.
        # x is front axle distance from the registered start line.
        return -math.atan2(height(x)-height(x-.27), .27)

    def step(self, engine, now, dt, config):
        if not engine.active:
            target_r = target_p = 0.
        else:
            horizon = config.lead
            v0, w0 = engine.command
            if isinstance(engine, Teleop):
                # Current held command only; do not invent the next key press.
                v1=v0+clamp(engine.axes[0]*engine.config.speed-v0,-engine.ACCELERATION*horizon,engine.ACCELERATION*horizon)
                w1=w0+clamp(engine.axes[1]*engine.yaw_speed-w0,-engine.ANGULAR_ACCELERATION*horizon,engine.ANGULAR_ACCELERATION*horizon)
                scale=min(1.,engine.MAX_SPEED/(abs(v1)+.198*abs(w1))) if v1 or w1 else 1.
                v1,w1=v1*scale,w1*scale
                grade_delta=0.
            else:
                future=copy.copy(engine);future.history=[]
                pose=Pose(**asdict(engine.pose));future.pose=Pose(**asdict(pose))
                at=now;remaining=horizon
                while remaining>1e-6:
                    step=min(.05, remaining);v,w=future.command
                    pose.x+=v*math.cos(pose.yaw+w*step*.5)*step
                    pose.y+=v*math.sin(pose.yaw+w*step*.5)*step
                    pose.yaw+=w*step;pose.v=v;pose.w=w;at+=step
                    future.step(at,pose);remaining-=step
                v1,w1=future.command
                grade_delta=0.
                if engine.kind=='bump':
                    along=lambda p:(p.x-engine.origin.x)*math.cos(engine.origin.yaw)+(p.y-engine.origin.y)*math.sin(engine.origin.yaw)
                    grade_delta=self.model_body_pitch(along(pose),engine.config)-self.model_body_pitch(along(engine.pose),engine.config)
            target_r=-math.degrees(math.atan2(v1*w1,9.81))
            target_p=math.degrees(math.atan2((v1-v0)/horizon,9.81)-grade_delta)
            if config.swap_axes:target_r,target_p=target_p,target_r
            target_r=clamp(target_r*config.roll_sign,-config.angle_limit,config.angle_limit)
            target_p=clamp(target_p*config.pitch_sign,-config.angle_limit,config.angle_limit)
        alpha=1-math.exp(-max(.001,dt)/.10)
        limit=12.*dt # deg/s, separate from the ESP32 PWM rate limit
        self.roll+=clamp(alpha*(target_r-self.roll),-limit,limit)
        self.pitch+=clamp(alpha*(target_p-self.pitch),-limit,limit)
        return self.roll,self.pitch
