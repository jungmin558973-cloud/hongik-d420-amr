"""NTP-style UTC offset; original monotonic and ROS stamps stay untouched."""
import bisect
import math


def estimate(samples):
    # Windows send, Jetson receive, Jetson send, Windows receive (UTC seconds).
    valid=[]
    for a,b,c,d in samples:
        rtt=(d-a)-(c-b)
        if all(math.isfinite(x) for x in (a,b,c,d)) and d>=a and c>=b and rtt>=0:
            valid.append((rtt,((b-a)+(c-d))/2))
    if len(valid)<3:raise ValueError('At least 3 valid clock probes required')
    best=sorted(valid)[:max(3,len(valid)//3)]
    offsets=sorted(x[1] for x in best);offset=offsets[len(offsets)//2]
    uncertainty=max(x[0]/2+abs(x[1]-offset) for x in best)
    return {'jetson_minus_windows_s':offset,'uncertainty_s':uncertainty,
            'quality':'good' if uncertainty<=.02 else 'uncertain','probes':len(valid)}


def merge(frames,robot,clock,hz=30,max_gap=.1):
    """Explicit uniform timeline, nearest frame and robot sample; no gap bridging."""
    if not frames:return []
    offset=clock.get('jetson_minus_windows_s',0.)
    robot=sorted(robot,key=lambda r:r['utc']-offset);rt=[r['utc']-offset for r in robot]
    frames=sorted(frames,key=lambda r:r['utc']);ft=[r['utc'] for r in frames]
    def nearest(ts,t):
        if not ts:return None
        k=bisect.bisect_left(ts,t);opts=[i for i in (k-1,k) if 0<=i<len(ts)]
        return min(opts,key=lambda i:abs(ts[i]-t))
    result=[];t0=ft[0]
    for n in range(int((ft[-1]-t0)*hz)+1):
        t=t0+n/hz;fi=nearest(ft,t);ri=nearest(rt,t)
        fd=ft[fi]-t;rd=rt[ri]-t if ri is not None else None
        frame_ok=abs(fd)<=max_gap;robot_ok=rd is not None and abs(rd)<=max_gap
        r=robot[ri] if robot_ok else {}
        f=frames[fi] if frame_ok else {}
        result.append({'utc':t,'t':t-t0,'frame_seq':f.get('seq'),
                       'frame_delta_ms':fd*1000,'robot_delta_ms':rd*1000 if rd is not None else None,
                       'match':'nearest','interpolated':False,'clock_quality':clock.get('quality','unknown'),
                       'sync_uncertainty_ms':clock.get('uncertainty_s',float('inf'))*1000,
                       'valid':bool(f.get('valid') and frame_ok),'robot_valid':robot_ok,
                       'displacement_px':f.get('displacement_px') if f.get('valid') and frame_ok else None,
                       'left_px':f.get('left_px'),'right_px':f.get('right_px'),
                       'x':r.get('x'),'v':r.get('v'),'requested_v':r.get('requested_v'),
                       'final_v':r.get('final_v'),'roll_deg':r.get('roll_deg'),'pitch_deg':r.get('pitch_deg'),
                       'state':r.get('state'),'segment':r.get('segment','forward')})
    return result
