"""Humble-compatible odom-frame Nav2 configuration, without a static map."""
from copy import deepcopy
import math


def parameters(source):
    config=deepcopy(source)
    config['/d420_nav/bt_navigator']['ros__parameters']['global_frame']='odom'
    controller=config['/d420_nav/controller_server']['ros__parameters']
    controller['FollowPath']['desired_linear_vel']=.40
    for which,size in [('global',16),('local',8)]:
        p=config[f'/d420_nav/{which}_costmap/{which}_costmap']['ros__parameters']
        p.update(global_frame='odom',rolling_window=True,width=size,height=size,
                 update_frequency=5.0,track_unknown_space=False)
        p.pop('static_layer',None)
        p['plugins']=['obstacle_layer','inflation_layer']
        # Live obstacle information is retained; absence of a saved map does not
        # disable collision checking. Unknown cells are traversed only with the
        # independently fresh local sensor/motor guard at execution time.
        p['obstacle_layer']['scan']['obstacle_max_range']=6.0
        p['obstacle_layer']['scan']['raytrace_max_range']=7.0
    return config


def relative_l_goals(origin,first,second,approach=0.):
    """Odom goals from a stationary start; optionally stop short of the hill.

    approach shortens the final leg to a measured hold point. For a hill,
    it must include the front-body overhang and clearance before ramp entry.
    This helper never authorizes ramp traversal or publishes motor commands.
    """
    for v,lo,hi in [(first,.2,6.),(second,.2,6.),(approach,0.,6.)]:
        if type(v) not in (int,float) or not math.isfinite(v) or not lo<=v<=hi:
            raise ValueError('경로 거리 범위를 확인해 주세요.')
    if approach>=second:raise ValueError('언덕 전 정지 거리가 두 번째 직선보다 깁니다.')
    if not isinstance(origin,dict):raise ValueError('출발 위치·방향을 확인해 주세요.')
    if any(type(origin.get(k)) not in (int,float) or not math.isfinite(origin[k]) for k in ('x','y','yaw')):
        raise ValueError('출발 위치·방향을 확인해 주세요.')
    x,y,a=origin['x'],origin['y'],origin['yaw'];right=a-math.pi/2
    x1,y1=x+first*math.cos(a),y+first*math.sin(a)
    wrap=lambda v:math.atan2(math.sin(v),math.cos(v))
    return [dict(x=x1,y=y1,yaw=wrap(a)),dict(x=x1,y=y1,yaw=wrap(right)),
            dict(x=x1+(second-approach)*math.cos(right),
                 y=y1+(second-approach)*math.sin(right),yaw=wrap(right))]
