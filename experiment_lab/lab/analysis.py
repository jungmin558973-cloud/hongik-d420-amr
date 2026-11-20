"""Quality-aware forward-only KPIs and conservative, approval-only suggestions."""
import math
import statistics as st
from collections import defaultdict
import itertools
import numpy as np


def summary(values):
    v=[float(x) for x in values if x is not None and math.isfinite(float(x))]
    return {'n':len(v),'mean':st.mean(v) if v else None,'median':st.median(v) if v else None,
            'std':st.stdev(v) if len(v)>1 else None,
            'max':max(v) if v else None,'rms':math.sqrt(st.mean(x*x for x in v)) if v else None}


def metrics(frames,robot,events,config):
    # Return is recovery only, deliberately excluded from every experimental KPI.
    cutoff=next((e['utc'] for e in events if e['event']=='RETURNING' and 'utc' in e),float('inf'))
    f=[r for r in frames if r['utc']<cutoff and r.get('segment','forward')=='forward']
    r=[r for r in robot if r.get('segment','forward')=='forward' and r['utc']<cutoff]
    good=[x for x in f if x.get('valid') and x.get('displacement_px') is not None]
    disp=[x['displacement_px'] for x in good];s=summary(disp)
    profiles=[d for row in good for d in row.get('profile_displacement_px',[]) if d is not None and math.isfinite(d)]
    spatial=summary(profiles)
    peak=max(good,key=lambda x:abs(x['displacement_px'])) if good else None
    stops=[e for e in events if e['event']=='STOP_CONFIRMED' and e.get('segment')=='forward']
    zeros=[e for e in events if e['event']=='ZERO_COMMAND' and e.get('segment')=='forward']
    brakes=[e for e in events if e['event']=='BRAKE_REQUEST' and e.get('segment')=='forward']
    stop=stops[0] if stops else None;brake=brakes[0] if brakes else None;zero=zeros[0] if zeros else None
    settled=None
    if stop:
        # Need a continuous 1s window and no invalid/missing frame gaps >100ms.
        for i,x in enumerate(f):
            if x['utc']<stop.get('utc',float('inf')):continue
            w=[q for q in f[i:] if q['utc']<=x['utc']+1.]
            if len(w)<15 or w[-1]['utc']-w[0]['utc']<.95:continue
            if all(q.get('valid') and q.get('displacement_px') is not None and abs(q['displacement_px'])<=2. for q in w) and max(b['utc']-a['utc'] for a,b in zip(w,w[1:]))<=.1:
                settled=x['utc']-stop['utc'];break
    derivatives=[];jerks=[];last_a=None
    for a,b in zip(r,r[1:]):
        dt=b['utc']-a['utc']
        if not 0<dt<=.15 or a.get('v') is None or b.get('v') is None:continue
        acc=(b['v']-a['v'])/dt;derivatives.append(acc)
        if last_a is not None:jerks.append((acc-last_a)/dt)
        last_a=acc
    frequency=None
    if len(good)>=90:
        ts=np.array([x['utc'] for x in good]);dt=float(np.median(np.diff(ts)))
        if ts[-1]-ts[0]>=3 and np.max(np.diff(ts))<dt*1.5:
            y=np.array(disp)-np.mean(disp);p=np.abs(np.fft.rfft(y*np.hanning(len(y))))
            hz=np.fft.rfftfreq(len(y),dt);k=int(np.argmax(p[1:])+1)
            if p[k]>0:frequency=float(hz[k])
    actual=stop.get('x') if stop else None
    return {'scope':'forward_until_settled; return_is_recovery','units':'px (mm only after calibration)',
            'valid_ratio':len(good)/len(f) if f else 0.,'valid_frames':len(good),'total_frames':len(f),
            'max_displacement_px':max(map(abs,profiles or disp)) if profiles or disp else None,
            'rms_px':spatial['rms'] if profiles else s['rms'],
            'rms_basis':'all valid surface points over time' if profiles else 'per-frame scalar displacement',
            'max_rise_above_baseline_px':max(profiles) if profiles else None,
            'maximum_visible_blue_column_height_px':max((row['highest_px'] for row in good if row.get('highest_px') is not None),default=None),
            'maximum_height_mm':None,'height_mm_reason':'Requires validated same-plane cup reference calibration',
            'peak_to_peak_px':max(disp)-min(disp) if disp else None,
            'max_left_right_px':max((abs(x['left_px']-x['right_px']) for x in good if x.get('left_px') is not None and x.get('right_px') is not None),default=None),
            'min_rim_margin_px':min((x['rim_margin_px'] for x in good if x.get('rim_margin_px') is not None),default=None),
            'peak_utc':peak['utc'] if peak else None,'peak_frame':peak.get('seq') if peak else None,
            'settling_s':settled,'frequency_hz':frequency,
            'actual_distance_m':actual,'distance_error_m':actual-config['distance'] if actual is not None else None,
            'brake_request_x_m':brake.get('x') if brake else None,
            'stop_distance_m':actual-brake['x'] if actual is not None and brake else None,
            'zero_to_stop_s':stop['utc']-zero['utc'] if stop and zero and 'utc' in stop and 'utc' in zero else None,
            'actual_speed':summary([x.get('v') for x in r]),'acceleration':summary(derivatives),
            'deceleration':summary([-x for x in derivatives if x<0]),'jerk':summary(jerks),
            'tray_roll_deg':summary([x.get('roll_deg') for x in r]),
            'tray_pitch_deg':summary([x.get('pitch_deg') for x in r]),
            'duration_s':r[-1]['utc']-r[0]['utc'] if len(r)>1 else None,
            'unsupported':['motor_current','battery_voltage','physical_brake_feedback','chassis_imu'],
            'lid_contact':'video estimate only; not validated'}


def compare(runs):
    groups=defaultdict(list)
    for r in runs:
        c=r['config'];m=r['metrics'];actual=m.get('actual_speed',{}).get('max')
        stop=m.get('actual_distance_m')
        if actual is None or stop is None:continue
        key=(c['distance'],round(actual/.02)*.02,c['cup'],c['fill_pct'],c['camera_setting'],
             round(stop/.05)*.05,c.get('lid'),c.get('diameter_mm'),c.get('water_g'),
             c['s_curve'],c['tray'],c.get('s_curve_strength',100) if c['s_curve'] else 0,r.get('simulated',False))
        groups[key].append(r)
    result=[]
    for key,rs in groups.items():
        vals=[r['metrics'].get('rms_px') for r in rs];valid=[x for x in vals if x is not None]
        stats=summary(valid);outliers=[]
        if len(valid)>=4:
            q1,q3=np.percentile(valid,[25,75]);iqr=q3-q1
            outliers=[r['run_id'] for r in rs if r['metrics'].get('rms_px') is not None and not q1-1.5*iqr<=r['metrics']['rms_px']<=q3+1.5*iqr]
        result.append({'condition':list(key),'run_ids':[r['run_id'] for r in rs],
                       'rms_px':stats,'outliers':outliers,'valid_ratio':summary([r['metrics']['valid_ratio'] for r in rs]),
                       'repeat_count':len(rs),'simulated':rs[0].get('simulated',False)})
    return result


def recommend(runs,validation):
    eligible=[r for r in runs if not r.get('simulated') and r.get('status')=='COMPLETE'
              and r['metrics'].get('protocol',{}).get('valid_for_research_protocol',False)
              and r['metrics'].get('valid_ratio',0)>=.8 and r['metrics'].get('rms_px') is not None
              and abs(r['metrics'].get('distance_error_m') or 0)<=.05]
    groups=[g for g in compare(eligible) if g['repeat_count']>=3 and g['rms_px']['n']>=3]
    if not groups:return {'status':'INSUFFICIENT_DATA','reason':'Need >=3 comparable real validated runs with >=80% valid frames', 'apply_automatically':False}
    best=min(groups,key=lambda g:g['rms_px']['mean']);source=[r for r in eligible if r['run_id'] in best['run_ids']]
    base=source[0]['config'];stops=[r['metrics']['stop_distance_m'] for r in source if r['metrics'].get('stop_distance_m') is not None]
    if not stops:return {'status':'INSUFFICIENT_DATA','reason':'Measured stop distances required','apply_automatically':False}
    # Conservative finite grid: observed configuration neighbourhood, never faster.
    candidates=[]
    for accel,decel,j,lead in itertools.product((.08,.12,.20),(.15,.20,.25),(.2,.3,.5),(0.,.1,.2)):
        if accel>base['acceleration'] or decel>base['deceleration'] or j>base['jerk']:continue
        speed=min(base['speed'],validation.max_speed)
        from .core import Profile
        p=Profile(base['distance'],speed,accel,decel,j,base['s_curve'],strength=base.get('s_curve_strength',100))
        stopping=speed*speed/(2*decel)+speed*decel/j+.05
        if stopping>base['distance']*.7:continue
        score=accel+decel+j*.2+p.duration*.02+lead*.01
        candidates.append((score,accel,decel,j,lead,p))
    if not candidates:return {'status':'NO_SAFE_CANDIDATE','apply_automatically':False}
    _,a,d,j,lead,p=min(candidates,key=lambda x:x[0])
    candidate={**base,'speed':min(base['speed'],validation.max_speed),'acceleration':a,'deceleration':d,
               'jerk':j,'lead':lead,'angle_limit':min(base['angle_limit'],3.),'gain':min(base['gain'],1.),
               'brake_offset_m':min(.05,max(0.,st.mean(r['metrics']['distance_error_m'] for r in source)))}
    from .core import Config
    try:validation.check(Config.parse(candidate))
    except ValueError as e:return {'status':'VALIDATION_REQUIRED','reason':str(e),'apply_automatically':False}
    return {'status':'REVIEW_REQUIRED','candidate':candidate,'basis':best,
            'reason':'Finite conservative grid around repeated observed condition; lower acceleration/jerk with travel-time penalty',
            'measured_stop_range_m':[min(stops),max(stops)],'predicted_effect':'Unvalidated hypothesis; no guaranteed reduction',
            'brake_start_m':p.at(p.brake_time)[0],'confidence':'limited','apply_automatically':False}
