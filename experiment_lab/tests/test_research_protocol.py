from dataclasses import asdict
import json
import pytest
from lab.core import Config,Controller,Validation,Observation
from lab.storage import dump,Journal
from lab.protocol import validate_run
from lab.analysis import metrics


def test_short_distance_rejected_before_motor_ownership():
    c=Controller(Validation(.4,2,True,('OFF',),True,'mock'));checks=dict.fromkeys(('space_clear','physical_stop_ready','cup_secure','origin_checked','operator_present'),True)
    token=c.arm(0,checks)
    with pytest.raises(ValueError,match='Distance too short'):c.start(Config(distance=.05,speed=.4),'short_run',token,.1,Observation())
    assert c.owner is None and c.command==0 and not c.active


def test_point_peak_is_not_straight_line_average():
    f=[{'utc':0,'valid':True,'displacement_px':5,'profile_displacement_px':[0,0,5,0,0]}]
    m=metrics(f,[],[],asdict(Config()))
    assert m['max_rise_above_baseline_px']==5
    assert m['max_displacement_px']==5
    assert m['rms_px']==pytest.approx(5/(5**.5))
    assert m['maximum_height_mm'] is None


def test_protocol_rejects_missing_4k_imu_and_manual_stop(tmp_path):
    dump(tmp_path/'camera_device.json',{'device_name':'C920'})
    r=validate_run(tmp_path,[],asdict(Config()),[{'frame_width':640,'frame_height':480}])
    assert not r['valid_for_research_protocol']
    assert len(r['reasons'])>=3
    assert (tmp_path/'imu.csv').read_text(encoding='utf-8-sig').startswith('utc,mono')


def test_valid_protocol_has_actual_imu_and_4k(tmp_path):
    Journal(tmp_path).append('robot_raw.jsonl',{'topic':'/d420/tray/status','utc':1,'data':{'connected':True,'imu_fresh':True,'roll_deg':1,'pitch_deg':2}})
    events=[{'segment':'forward','event':'CRUISE_CONFIRMED','duration_s':1.6},{'segment':'forward','event':'ZERO_COMMAND'},{'segment':'forward','event':'BRAKE_REQUEST','x':1.6},{'segment':'forward','event':'STOP_CONFIRMED','x':1.67}]
    result=validate_run(tmp_path,events,asdict(Config()),[{'frame_width':3840,'frame_height':2160}])
    assert result['valid_for_research_protocol'] and result['imu_samples']==1
