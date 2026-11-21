from dataclasses import asdict
import json
import time
import pytest
from lab.core import Config,Controller,Validation,Observation
from lab.camera import Camera
from lab.storage import Store,dump,read_jsonl
from lab.vision import SYNTHETIC_ROI
from lab.offline import analyze


@pytest.mark.parametrize('speed',[.2,.3,.4])
@pytest.mark.parametrize('smooth',[False,True])
def test_single_abrupt_stop_after_distance_and_cruise(speed,smooth):
    c=Controller(Validation(.4,2,True,('OFF',),False,'test only'))
    o=Observation();cfg=Config(speed=speed,s_curve=smooth)
    checks=dict.fromkeys(('space_clear','physical_stop_ready','cup_secure','origin_checked','operator_present'),True)
    c.start(cfg,'test',c.arm(0,checks),.01,o)
    seen_motion=False
    for i in range(1,2000):
        now=.01+i*.02;c.beat(i,now)
        o.v+=(c.command-o.v)*.2;o.x+=o.v*.02
        c.tick(now,o)
        if c.state=='MOVING' and c.command>0:seen_motion=True
        if seen_motion and c.state=='MOVING':assert c.command>0
        if not c.active:break
    assert c.state=='COMPLETE'
    brakes=[e for e in c.events if e['event']=='BRAKE_REQUEST']
    assert len(brakes)==1 and 1.6<=brakes[0]['x']<1.61
    assert len([e for e in c.events if e['event']=='ZERO_COMMAND'])==1
    assert any(e['event']=='CRUISE_CONFIRMED' and e['duration_s']>=1.5 for e in c.events)
    assert not any(e['event']=='RETURNING' for e in c.events)


def test_deferred_recording_then_offline_preserves_original_evidence(tmp_path):
    store=Store(tmp_path/'data');rid,path=store.begin(asdict(Config()),True)
    dump(path/'roi.json',SYNTHETIC_ROI)
    camera=Camera(tmp_path/'preview')
    try:
        camera.start({'source':'synthetic','roi':SYNTHETIC_ROI})
        deadline=time.monotonic()+8
        while not camera.poll().get('ready') and time.monotonic()<deadline:time.sleep(.03)
        assert camera.poll().get('ready')
        camera.record(path,record_priority=True);time.sleep(.5);camera.stop_record()
    finally:camera.close()
    rows=read_jsonl(path/'camera_frames.jsonl');original=(path/'camera_frames.jsonl').read_bytes()
    assert rows and all(r['analysis_deferred'] and not r['valid'] for r in rows)
    assert (path/'webcam_raw.avi').exists() and not (path/'webcam_annotated.mp4').exists()
    clock={'quality':'good','jetson_minus_windows_s':0,'uncertainty_s':.001}
    pending=store.finalize(rid,'COMPLETE',clock)
    assert pending['analysis_status']=='PENDING_OFFLINE_ANALYSIS'
    path=store.path(rid)
    result=analyze(store,rid)
    assert result['analysis_status']=='COMPLETE' and result['valid_frames']>0
    assert (path/'camera_frames.jsonl').read_bytes()==original
    assert len(read_jsonl(path/'camera_analysis.jsonl'))==len(rows)
