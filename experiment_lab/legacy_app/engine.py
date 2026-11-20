"""Mapless D420 experiments. Pure control logic; no ROS imports or I/O."""
from dataclasses import dataclass, asdict
import math


def wrap(angle):
    return math.atan2(math.sin(angle), math.cos(angle))


def clamp(value, low, high):
    return max(low, min(high, value))


@dataclass
class Pose:
    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0
    v: float = 0.0
    w: float = 0.0


@dataclass
class Config:
    speed: float = 0.08
    distance: float = 1.00
    radius: float = 0.35
    direction: int = 1
    bump_gap: float = 1.20
    hill_length: float = 0.80
    hill_span: float = 0.60
    hill_angle: float = 15.0
    hill_length_mode: str = 'slope_surface'

    @property
    def bump_width(self):
        if self.hill_length_mode == 'total_base':
            return self.hill_length
        if self.hill_length_mode == 'slope_surface':
            return 2*self.hill_length*math.cos(math.radians(self.hill_angle))
        return 2*self.hill_length

    @property
    def bump_height(self):
        return self.bump_width/2*math.tan(math.radians(self.hill_angle))

    def serialized(self):
        return {**asdict(self), 'bump_width':self.bump_width, 'bump_height':self.bump_height}

    @classmethod
    def parse(cls, data, max_speed=0.30):
        if not isinstance(data, dict) or set(data) - set(cls.__dataclass_fields__):
            raise ValueError('설정 항목을 확인해 주세요.')
        cfg = cls(**data)
        limits = {'speed': (0.03, max_speed), 'distance': (1.0, 1.0),
                  'radius': (0.30, 0.60), 'bump_gap': (1.20, 1.20),
                  'hill_length': (0.20, 1.50), 'hill_span':(0.30,1.20),
                  'hill_angle': (1.0,25.0)}
        for name, (low, high) in limits.items():
            value = getattr(cfg, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f'{name}: {low}~{high} 범위의 유한한 숫자가 필요합니다.')
        if isinstance(cfg.direction, bool) or cfg.direction not in (-1, 1):
            raise ValueError('회전 방향을 확인해 주세요.')
        if cfg.hill_length_mode not in ('total_base','single_run','slope_surface'):
            raise ValueError('언덕 길이 기준을 확인해 주세요.')
        return cfg


class Engine:
    names = {'brake': '1m 후 급정지', 'turn': '1m 후 90° 턴', 'bump': '1.2m 후 언덕 10cm 진입'}
    labels = {'idle': '시험 선택', 'countdown': '출발 준비', 'approach': '직선 진입',
              'turn': '90° 선회', 'exit': '회전 후 직진', 'cross': '저속 통과',
              'observe': '정지 응답 기록', 'done': '시험 완료', 'stopped': '시험 중단'}

    def __init__(self):
        self.config = Config()
        self.kind = 'brake'
        self.mode = 'preview'
        self.state = 'idle'
        self.message = '시나리오를 선택해 주세요.'
        self.active = False
        self.origin = Pose()
        self.pose = Pose()
        self.command = (0.0, 0.0)
        self.history = []
        self.started = self.last_tick = self.phase_at = 0.0
        self.distance = self.turn_angle = self.progress = 0.0
        self.phase_distance = 0.0
        self.peak_speed = self.peak_acceleration = 0.0
        self.last_progress_at = 0.0
        self.last_progress_value = 0.0
        self.observation_good_since = None
        self.stop_distance = None
        self.stop_yaw = None
        self.turn_finished_angle = None
        self.finished_at = None
        self.phase_x = self.phase_y = 0.0

    def start(self, kind, mode, config, now, pose):
        if self.active:
            raise ValueError('진행 중인 시험을 먼저 중지해 주세요.')
        if kind not in self.names or mode not in ('preview', 'real'):
            raise ValueError('시나리오 또는 실행 모드가 올바르지 않습니다.')
        if not all(math.isfinite(v) for v in asdict(pose).values()):
            raise ValueError('유효한 현재 위치가 필요합니다.')
        if abs(pose.v) > 0.01 or abs(pose.w) > 0.03:
            raise ValueError('로봇이 완전히 정지한 뒤 실행해 주세요.')
        self.__init__()
        self.kind, self.mode, self.config = kind, mode, config
        self.origin = Pose(**asdict(pose))
        self.pose = Pose(**asdict(pose))
        self.phase_yaw = pose.yaw
        self.active = True
        self.state = 'countdown'
        self.message = '출발 위치 기준으로 시험합니다.'
        self.started = self.last_tick = self.phase_at = self.last_progress_at = now

    def stop(self, reason='사용자가 중지했습니다.'):
        if self.active:
            self.active = False
            self.state = 'stopped'
            self.message = reason
            self.finished_at = self.last_tick
        self.command = (0.0, 0.0)

    def phase(self, state, now):
        self.state, self.phase_at = state, now
        self.phase_distance, self.phase_yaw = self.distance, self.pose.yaw
        self.phase_x, self.phase_y = self.pose.x, self.pose.y
        self.last_progress_at = now
        self.last_progress_value = self.distance
        if state == 'turn':
            self.turn_angle = 0.0

    def observe(self, now):
        self.stop_distance = self.distance
        self.stop_yaw = self.pose.yaw
        self.command = (0.0, 0.0)
        self.phase('observe', now)

    def step(self, now, pose):
        previous = self.pose
        self.pose = Pose(**asdict(pose))
        dt = clamp(now - self.last_tick, 0.001, 0.10)
        elapsed = now - self.started
        self.last_tick = now
        if not self.active:
            return self.command
        if not all(math.isfinite(v) for v in asdict(pose).values()):
            self.stop('오도메트리 값이 유효하지 않습니다.')
            return self.command
        moved = math.hypot(pose.x - previous.x, pose.y - previous.y)
        if moved > 0.10 or abs(wrap(pose.yaw - previous.yaw)) > 0.25:
            self.stop('위치가 불연속적으로 변했습니다. 원점을 다시 확인해 주세요.')
            return self.command
        along = (pose.x - previous.x) * math.cos(previous.yaw) + (pose.y - previous.y) * math.sin(previous.yaw)
        lateral = -(pose.x - previous.x) * math.sin(previous.yaw) + (pose.y - previous.y) * math.cos(previous.yaw)
        if pose.v < -0.015 or along < -0.004 or abs(lateral) > 0.007:
            self.stop('전진 명령과 다른 이동이 감지됐습니다. 차체 방향을 확인해 주세요.')
            return self.command
        self.distance += moved
        self.peak_speed = max(self.peak_speed, abs(pose.v))
        self.peak_acceleration = max(self.peak_acceleration, abs(pose.v - previous.v) / dt)
        if abs(pose.v) > 0.34 or abs(pose.w) > 0.40 or elapsed > 90 or self.distance > 2.5:
            self.stop('시험의 속도·시간·이동 범위를 초과했습니다.')
            return self.command
        v = w = 0.0
        cfg = self.config
        if self.state == 'countdown':
            if abs(pose.v) > 0.015 or abs(pose.w) > 0.04:
                self.stop('출발 준비 중 로봇 움직임이 감지됐습니다.')
            elif now - self.phase_at >= 1.5:
                self.phase('cross' if self.kind == 'bump' else 'approach', now)
        elif self.state in ('approach', 'cross', 'exit'):
            target = cfg.bump_gap + 0.10 if self.kind == 'bump' else cfg.distance
            travelled = ((pose.x-self.phase_x)*math.cos(self.phase_yaw)
                         +(pose.y-self.phase_y)*math.sin(self.phase_yaw))
            remaining = target - travelled
            target_yaw = self.origin.yaw + (cfg.direction * math.pi / 2 if self.state == 'exit' else 0)
            heading_error = wrap(target_yaw - pose.yaw)
            if abs(heading_error) > 0.35:
                self.stop('직진 중 방향 오차가 커졌습니다.')
            elif remaining <= (0.006 if self.kind == 'bump' else 0.0):
                if self.kind == 'turn' and self.state == 'approach':
                    self.phase('turn', now)
                    v = min(cfg.speed, self.command[0])
                    w = cfg.direction * min(0.25, cfg.speed/cfg.radius)
                else:
                    self.observe(now)
            else:
                desired = cfg.speed
                if self.kind == 'bump':
                    # Course origin is the front axle at the start line.
                    if travelled >= cfg.bump_gap - 0.15:
                        desired = min(desired, 0.05)
                if self.kind == 'bump':
                    desired = min(desired, max(0.018, 0.8 * remaining))
                v = self.command[0] + clamp(desired - self.command[0], -0.08 * dt, 0.05 * dt)
                w = clamp(1.2 * heading_error, -0.12, 0.12)
            self.progress = clamp(travelled / target, 0, 1)
        elif self.state == 'turn':
            self.turn_angle += cfg.direction * wrap(pose.yaw - previous.yaw)
            remaining = math.pi / 2 - self.turn_angle
            if self.turn_angle < -0.08 or self.turn_angle > math.pi / 2 + 0.10:
                self.stop('회전 방향 또는 각도를 확인해 주세요.')
            elif remaining <= math.radians(1.5):
                self.turn_finished_angle = self.turn_angle
                self.observe(now)
            else:
                desired_w = min(0.25, cfg.speed / cfg.radius, max(0.045, 1.2 * remaining))
                w = cfg.direction * min(desired_w, abs(self.command[1]) + 0.45 * dt)
                # A moving quarter-circle, rather than an in-place spin.
                desired_v = min(cfg.speed, cfg.radius * abs(w))
                v = self.command[0] + clamp(desired_v - self.command[0], -0.08 * dt, 0.05 * dt)
            self.progress = clamp(self.turn_angle / (math.pi / 2), 0, 1)
        elif self.state == 'observe':
            stationary = abs(pose.v) < 0.01 and abs(pose.w) < 0.03
            if stationary and self.observation_good_since is None:
                self.observation_good_since = now
            if not stationary:
                self.observation_good_since = None
            if self.observation_good_since is not None and now - self.observation_good_since >= 1.0:
                self.state = 'done'
                self.active = False
                self.finished_at = now
                self.progress = 1.0
                self.message = ('총 1.30m 주행 완료 · 언덕 진입/정지 유지 상태는 현장에서 확인해 주세요.'
                                if self.kind == 'bump' else '주행 및 정지 응답 기록을 완료했습니다.')
            elif now - self.phase_at > 5:
                self.stop('정지 명령 이후에도 움직임이 감지됩니다.')
        if self.active and self.state in ('approach', 'cross', 'exit', 'turn'):
            metric = self.turn_angle if self.state == 'turn' else self.distance
            if abs(metric - self.last_progress_value) > 0.005:
                self.last_progress_at, self.last_progress_value = now, metric
            if now - self.last_progress_at > 4 and (abs(v) > 0.025 or abs(w) > 0.04):
                self.stop('이동 응답이 없습니다. SERIAL 모드·전원·바퀴 상태를 확인해 주세요.')
        self.command = (clamp(v, 0.0, 0.30), clamp(w, -0.30, 0.30)) if self.active else (0.0, 0.0)
        if self.state == 'observe':
            self.command = (0.0, 0.0)  # Abrupt stop bypasses the normal slew limiter.
        self.history.append({'t': round(elapsed, 3), 'x': pose.x, 'y': pose.y, 'yaw': pose.yaw,
                             'v': pose.v, 'w': pose.w, 'cmd_v': self.command[0],
                             'cmd_w': self.command[1], 'phase': self.state})
        return self.command

    def snapshot(self, now):
        dx, dy = self.pose.x - self.origin.x, self.pose.y - self.origin.y
        c, s = math.cos(self.origin.yaw), math.sin(self.origin.yaw)
        label = self.labels[self.state]
        if self.kind == 'bump' and self.state == 'cross':
            label = '언덕까지 정속 진입' if c*dx+s*dy < 1.05 else '언덕 앞 감속' if c*dx+s*dy < 1.20 else '언덕 10cm 진입'
        return {'kind': self.kind, 'mode': self.mode, 'state': self.state,
                'label': label, 'message': self.message, 'active': self.active,
                'config': self.config.serialized(), 'elapsed': max(0, (now if self.active else self.finished_at or self.started) - self.started) if self.started else 0,
                'pose': {'x': c * dx + s * dy, 'y': -s * dx + c * dy,
                         'yaw': wrap(self.pose.yaw - self.origin.yaw)},
                'v': self.pose.v, 'w': self.pose.w, 'command': self.command,
                'distance': self.distance, 'turn_deg': math.degrees(self.turn_angle),
                'progress': self.progress, 'peak_speed': self.peak_speed,
                'peak_acceleration_estimate': self.peak_acceleration,
                'samples': len(self.history),
                'course': {'start': 0.0, 'event_line': 1.0, 'hill_start': 1.2,
                           'hill_stop': 1.3, 'front_axle_offset': 0.135},
                'chart': self.history[-180::3]}


def scan_collision(points, v, w, margin=0.045):
    """Swept padded CAD rectangle over 1.2 s. Points already in base_footprint."""
    for step in range(13):
        t = step * 0.1
        angle = w * t
        if abs(w) > 1e-6:
            x, y = v / w * math.sin(angle), v / w * (1 - math.cos(angle))
        else:
            x, y = v * t, 0.0
        c, s = math.cos(angle), math.sin(angle)
        for px, py in points:
            dx, dy = px - x, py - y
            bx, by = c * dx + s * dy, -s * dx + c * dy
            if -0.220 - margin <= bx <= 0.282 + margin and abs(by) <= 0.208 + margin:
                return True
    return False
