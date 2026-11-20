"""Auditable capture-protocol validation; missing evidence never counts as a pass."""
import csv
import json
from .storage import read_jsonl,dump


def export_imu(path):
    fields=['utc','mono','ros_stamp','topic','valid','roll_deg','pitch_deg','qx','qy','qz','qw','gx','gy','gz','ax','ay','az']
    rows=[]
    for r in read_jsonl(path/'robot_raw.jsonl'):
        d=r.get('data',{});topic=r.get('topic','')
        if r.get('type')=='sensor_msgs/msg/Imu':
            row={k:r.get(k) for k in ('utc','mono','ros_stamp','topic')};row['valid']=True
            for prefix,key,axes in [('q','orientation','xyzw'),('g','angular_velocity','xyz'),('a','linear_acceleration','xyz')]:
                for axis in axes:row[prefix+axis]=d.get(key,{}).get(axis)
            row['orientation_valid']=d.get('orientation_covariance',[0])[0]!=-1
            # Keep unknown/unsupported covariance flags in raw JSONL; do not derive missing attitude.
            rows.append({k:v for k,v in row.items() if k in fields})
        elif topic=='/d420/tray/status':
            rows.append({**{k:r.get(k) for k in ('utc','mono','ros_stamp','topic')},
                         'valid':bool(d.get('connected') and d.get('imu_fresh')),
                         'roll_deg':d.get('roll_deg') if d.get('imu_fresh') else None,
                         'pitch_deg':d.get('pitch_deg') if d.get('imu_fresh') else None})
    with (path/'imu.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    valid=sum(bool(r.get('valid')) for r in rows)
    dump(path/'imu_availability.json',{'samples':len(rows),'valid_samples':valid,'available':bool(valid),'note':'Missing/stale IMU is unavailable, never zero motion.'})
    return valid


def validate_run(path,events,config,frames):
    forward=[e for e in events if e.get('segment')=='forward']
    reasons=[]
    if not any(e['event']=='CRUISE_CONFIRMED' and e.get('duration_s',0)>=config.get('min_cruise_s',1.5) for e in forward):reasons.append('No measured >=1.5s cruise confirmation')
    if len([e for e in forward if e['event']=='ZERO_COMMAND'])!=1:reasons.append('Expected exactly one final zero-command event')
    stops=[e for e in forward if e['event']=='STOP_CONFIRMED']
    if len(stops)!=1:reasons.append('Exactly one physically observed stop confirmation required')
    elif stops[0].get('x') is None:reasons.append('Actual stop position missing')
    if config.get('stop_semantics','stop_at_distance')=='brake_at_distance':
        brakes=[e for e in forward if e['event']=='BRAKE_REQUEST']
        if len(brakes)!=1 or brakes[0].get('x') is None or abs(brakes[0]['x']-config['distance'])>.05:
            reasons.append('Single brake command at target distance within 5cm not verified')
    elif stops and stops[0].get('x') is not None and abs(stops[0]['x']-config['distance'])>.05:reasons.append('Actual stop position differs from target by >5cm')
    if any(e['event']=='ABORT' for e in events):reasons.append('Aborted/safety-stopped trial')
    if config.get('scenario')!='planned_stop':reasons.append('Not an automatic planned-stop trial')
    device=json.loads((path/'camera_device.json').read_text(encoding='utf-8')) if (path/'camera_device.json').exists() else {}
    # Actual frame dimensions are logged by the capture worker, never requested dimensions.
    dimensions=[(r.get('frame_width',0),r.get('frame_height',0)) for r in frames]
    if config.get('require_4k',False) and (not dimensions or any(w<3840 or h<2160 for w,h in dimensions)):reasons.append('Native >=3840x2160 capture not verified')
    if config.get('record_priority') and (not dimensions or any(w<1920 or h<1080 for w,h in dimensions)):reasons.append('Native >=1920x1080 record-priority capture not verified')
    samples=export_imu(path)
    if not samples:reasons.append('IMU CSV has no actual samples')
    result={'valid_for_research_protocol':not reasons,'reasons':reasons,'repeats_required':3,
            'operator_requested_capture':bool(config.get('commissioning')),'hardware_prevalidated':not bool(config.get('commissioning')),
            'measured_cruise_min_s':1.5,'exactly_one_planned_stop':True,'imu_samples':samples,
            'camera_identity':device.get('device_name'),'automatic_only':True}
    dump(path/'protocol_validation.json',result);return result
