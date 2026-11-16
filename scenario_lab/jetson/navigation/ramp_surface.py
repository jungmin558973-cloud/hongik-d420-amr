"""Offline terrain labels against a measured ramp; no obstacle clearing or IO.

Points must already be in a gravity-aligned odom frame using full chassis
attitude. The present chassis bridge supplies yaw only, so the live pipeline
must not enable this exclusion until roll/pitch and ramp pose are validated.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Ramp:
    x: float
    y: float
    yaw: float
    run: float       # horizontal length, not slope-surface length
    rise: float
    width: float
    verified: bool = False

    def __post_init__(self):
        if type(self.verified) is not bool:raise ValueError('언덕 검증 상태가 유효하지 않습니다.')
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in
               (self.x,self.y,self.yaw,self.run,self.rise,self.width)):
            raise ValueError('언덕 치수·위치가 유효하지 않습니다.')
        if self.run<=0 or self.rise<=0 or self.width<.516:
            raise ValueError('양의 길이·높이와 차체 여유 폭이 필요합니다.')

    @property
    def slope_deg(self):return math.degrees(math.atan2(self.rise,self.run))

    def expected_height(self,x,y):
        dx,dy=x-self.x,y-self.y;c,s=math.cos(self.yaw),math.sin(self.yaw)
        along,across=c*dx+s*dy,-s*dx+c*dy
        # Outside measured ramp bounds, no permission to discard a point.
        if not 0<=along<=self.run or abs(across)>self.width/2:return None
        return self.rise*along/self.run


def label_points(points,ramp,*,attitude_valid=False,max_verified_slope_deg=None):
    """Return labels; caller must retain OBSTACLE and UNKNOWN as blocked.

    No height threshold is raised globally. A person standing on the ramp
    remains an obstacle above the measured local surface.
    """
    enabled=(ramp.verified and attitude_valid and type(max_verified_slope_deg) in (int,float)
             and math.isfinite(max_verified_slope_deg) and 0<ramp.slope_deg<=max_verified_slope_deg)
    result=[]
    for p in points:
        if not isinstance(p,(list,tuple)) or len(p)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) for v in p):
            result.append('UNKNOWN');continue
        expected=ramp.expected_height(p[0],p[1])
        if expected is None:
            result.append('FLOOR' if abs(p[2])<=.03 else 'OBSTACLE' if p[2]>.03 else 'UNKNOWN')
        elif not enabled:result.append('UNKNOWN')
        elif abs(p[2]-expected)<=.03:result.append('RAMP')
        elif p[2]>expected+.03:result.append('OBSTACLE')
        else:result.append('UNKNOWN')
    return result
