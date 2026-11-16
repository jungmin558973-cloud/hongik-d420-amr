"""Near/reverse following clearance. Coordinates use base_footprint, meters."""
import math


def limit_command(command,check):
    """Keep a checked slower motion instead of repeatedly accelerating into a stop."""
    reason=check(*command)
    if not reason:return command,''
    for scale in (.75,.5,.25):
        candidate=tuple(value*scale for value in command)
        if not check(*candidate):return candidate,'여유 공간에 맞춰 감속합니다.'
    return (0.,0.),reason


def coverage_bin(bearing, reverse=False):
    center=math.pi if reverse else 0.
    angle=math.atan2(math.sin(bearing-center),math.cos(bearing-center))
    # Three independent sectors: a few returns on one side are insufficient.
    if -.75<=angle<-.25:return 0
    if -.25<=angle<=.25:return 1
    if .25<angle<=.75:return 2
    return None


def motion_reason(points,v,w,coverage,fresh):
    if abs(v)<1e-6 and abs(w)<1e-6:return ''
    if not fresh:return '근접 추종용 라이다 관측이 지연돼 정지합니다.'
    reverse=v<0
    if reverse and abs(w)>1e-6:return '후진은 직선으로만 허용합니다.'
    sectors=coverage.get('rear' if reverse else 'front',[])
    if len(sectors)!=3 or any(total<15 or valid<total*.8 for total,valid in sectors):
        return ('후방' if reverse else '전방')+' 관측이 부족해 대기합니다.'
    # CAD footprint + close clearance + measured-command braking allowance.
    margin=.10+v*v/(2*.25)
    for i in range(7):
        t=i*.1;angle=w*t
        x,y=(v/w*math.sin(angle),v/w*(1-math.cos(angle))) if abs(w)>1e-6 else (v*t,0.)
        c,s=math.cos(angle),math.sin(angle)
        for px,py in points:
            if not math.isfinite(px) or not math.isfinite(py):continue
            dx,dy=px-x,py-y
            bx,by=c*dx+s*dy,-s*dx+c*dy
            if -.220-margin<=bx<=.282+margin and abs(by)<=.208+margin:
                return ('후방' if reverse else '주변')+' 여유 공간이 부족해 대기합니다.'
    return ''
