import time
from lab.camera import Camera
from lab.vision import SYNTHETIC_ROI


def wait_until(fn,timeout=8):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if fn():return True
        time.sleep(.05)
    return False


def test_process_record_close_failure_reconnect(tmp_path):
    cam=Camera(tmp_path/'preview');out=tmp_path/'run';out.mkdir()
    try:
        cam.start({'source':'synthetic','roi':SYNTHETIC_ROI,'fps':30})
        assert wait_until(lambda:cam.poll().get('ready'))
        cam.record(out);time.sleep(.35);cam.stop_record()
        assert (out/'webcam_raw.mp4').stat().st_size>500
        assert (out/'webcam_annotated.mp4').stat().st_size>500
        assert (out/'camera_frames.jsonl').read_text().count('\n')>=5
        cam.start({'source':str(tmp_path/'missing.mp4'),'backend':'ANY'})
        assert wait_until(lambda:not cam.process.is_alive())
        assert not cam.poll()['ready'] and cam.status.get('error')
        cam.start({'source':'synthetic','roi':SYNTHETIC_ROI})
        assert wait_until(lambda:cam.poll().get('ready'))
    finally:cam.close()
