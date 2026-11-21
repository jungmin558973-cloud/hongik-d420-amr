import json
from dataclasses import asdict
import cv2
import numpy as np
from lab.analysis_view import review
from lab.backend import MockBackend
from lab.core import Config
from lab.runtime import Manager
from lab.server import create_app
from lab.storage import dump


def fixture_run(m):
    rid,path=m.store.begin(asdict(Config()),True)
    dump(path/'manifest.json',{'status':'COMPLETE','clock':{'jetson_minus_windows_s':2,'uncertainty_s':.03,'quality':'uncertain'}})
    def write(name,rows):
        (path/name).write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
    write('camera_analysis.jsonl',[{'utc':100+i,'seq':i,'valid':i!=2,'profile_displacement_px':v,'surface_points_px':[[5,5,True]]} for i,v in enumerate([[999],[2,4],[500],[3,5]])])
    write('events.jsonl',[{'event':'BRAKE_REQUEST','segment':'forward','source':'jetson','utc':103},{'event':'STOP_CONFIRMED','segment':'forward','source':'jetson','utc':104}])
    write('robot_telemetry.jsonl',[{'utc':103,'v':.2}])
    return rid,path


def test_stop_window_excludes_outside_peak_and_invalid_points(tmp_path):
    m=Manager(tmp_path,MockBackend(),False);rid,path=fixture_run(m)
    result=review(path)
    assert result['window']['max_rise_px']==5
    assert result['window']['valid_frames']==2 and result['window']['total_frames']==3
    assert result['telemetry'][0]['t']==1
    assert result['series'][2]['rise'] is None
    m.close()


def test_review_routes_do_not_need_motion_and_frame_bounds_are_checked(tmp_path):
    m=Manager(tmp_path,MockBackend(),False);rid,path=fixture_run(m)
    writer=cv2.VideoWriter(str(path/'webcam_raw.avi'),cv2.VideoWriter_fourcc(*'MJPG'),30,(64,48))
    for _ in range(4):writer.write(np.zeros((48,64,3),np.uint8))
    writer.release()
    client=create_app(m).test_client()
    assert client.get('/analysis-lab').status_code==200
    assert client.get('/api/review/'+rid).json['window']['max_rise_px']==5
    image=client.get('/api/review/'+rid+'/frame/1.jpg')
    assert image.status_code==200 and image.mimetype=='image/jpeg' and image.data.startswith(b'\xff\xd8')
    assert client.get('/api/review/'+rid+'/frame/999.jpg').status_code==409
    assert not m.active
    m.close()
