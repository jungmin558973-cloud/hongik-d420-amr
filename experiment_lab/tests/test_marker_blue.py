import cv2
import numpy as np
from lab.marker_blue import MarkerBlue


def fixture(blue=True):
    frame=np.full((480,640,3),240,np.uint8);dictionary=cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for i,(x,y) in enumerate(((100,60),(500,60),(100,370),(500,370))):
        tile=cv2.aruco.generateImageMarker(dictionary,i,40)
        frame[y:y+40,x:x+40]=cv2.cvtColor(tile,cv2.COLOR_GRAY2BGR)
    if blue:cv2.rectangle(frame,(260,240),(380,350),(220,80,20),-1)
    return frame


def test_board_blue_baseline_and_missing_signals():
    t=MarkerBlue({'reference_required':False})
    for _ in range(15):r,_=t.process(fixture())
    assert r['valid'] and abs(r['displacement_px'])<1e-6
    assert len(r['marker_ids'])==4 and r['blue_bbox'][0]==260
    r,_=t.process(fixture(False));assert not r['valid'] and r['displacement_px'] is None
    frame=fixture();frame[:]=240;r,_=t.process(frame);assert not r['valid']


def test_reference_survives_out_of_view_and_record_reset(monkeypatch):
    from lab.vision import Tracker
    clock=[0.];monkeypatch.setattr('lab.marker_blue.time.perf_counter',lambda:clock[0])
    tracker=Tracker({'method':'marker_blue'})
    tracker.process(fixture());tracker.marker_blue.begin_reference()
    for i in range(40):
        clock[0]=i*.06;result,_=tracker.process(fixture())
    assert result['reference_locked'] and result['valid']
    missing=np.full((480,640,3),240,np.uint8)
    result,_=tracker.process(missing);assert not result['valid'] and result['reference_locked']
    tracker.reset(tracker.settings) # camera record-start resets tracking, but must preserve reference
    moving=fixture();cv2.rectangle(moving,(260,230),(380,350),(220,80,20),-1)
    result,_=tracker.process(moving)
    assert result['valid'] and result['displacement_px']>8 # first visible peak is not zeroed


def test_offline_tail_reference_requires_actual_stopped_odom(tmp_path):
    import json
    import pytest
    from lab.offline import tail_reference
    video=tmp_path/'webcam_raw.avi'
    w=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'MJPG'),30,(640,480))
    rows=[]
    for i in range(150):w.write(fixture());rows.append({'utc':100+i/30,'seq':i})
    w.release()
    (tmp_path/'events.jsonl').write_text(json.dumps({'event':'STOP_CONFIRMED','segment':'forward','utc':100})+'\n')
    telemetry=[{'utc':100+i/30,'v':0.} for i in range(150)]
    (tmp_path/'robot_telemetry.jsonl').write_text('\n'.join(json.dumps(r) for r in telemetry)+'\n')
    settings=tail_reference(tmp_path,{'method':'marker_blue'},rows,video,{'jetson_minus_windows_s':0})
    assert len(settings['reference_profile'])==101
    telemetry[-1]['v']=.2
    (tmp_path/'robot_telemetry.jsonl').write_text('\n'.join(json.dumps(r) for r in telemetry)+'\n')
    with pytest.raises(ValueError,match='오도메트리'):tail_reference(tmp_path,{'method':'marker_blue'},rows,video,{})
