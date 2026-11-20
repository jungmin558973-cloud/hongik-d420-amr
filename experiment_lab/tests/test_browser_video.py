import json
import cv2
import numpy as np
from lab.browser_video import make_browser_video


def test_h264_playback_retains_irregular_actual_frame_times(tmp_path):
    times=[0.,.04,.14,.18,.26]
    p=tmp_path/'webcam_annotated.mp4';w=cv2.VideoWriter(str(p),cv2.VideoWriter_fourcc(*'mp4v'),30,(64,48))
    for i in range(len(times)):w.write(np.full((48,64,3),i*40,np.uint8))
    w.release();original=p.read_bytes()
    (tmp_path/'camera_analysis.jsonl').write_text(''.join(json.dumps({'utc':100+t,'seq':i})+'\n' for i,t in enumerate(times)))
    info=make_browser_video(tmp_path)
    assert info['frames']==len(times) and p.read_bytes()==original
    cap=cv2.VideoCapture(str(tmp_path/info['file']));actual=[]
    while True:
        ok,_=cap.read()
        if not ok:break
        actual.append(cap.get(cv2.CAP_PROP_POS_MSEC)/1000)
    cap.release()
    assert len(actual)==len(times)
    assert max(abs(a-b) for a,b in zip(actual,times))<.003
