import threading
from lab.backend import MockBackend
from lab.runtime import Manager


def test_c920_plugged_after_launch_reconnects_without_selecting_laptop(tmp_path, monkeypatch):
    m=Manager(tmp_path,MockBackend(),False)
    m.backend.simulated=False
    m.auto_camera_requested=True
    selected=[];done=threading.Event()
    monkeypatch.setattr('lab.devices.resolve',lambda *args:dict(source=1,device_name='HD Pro Webcam C920',backend='DSHOW'))
    monkeypatch.setattr(m,'start_camera',lambda options:(selected.append(options),done.set()))
    m.retry_camera_if_idle({'ready':False})
    assert done.wait(2)
    assert selected[0]['device_name']=='HD Pro Webcam C920'
    m.close()


def test_reconnect_does_not_replace_camera_during_experiment(tmp_path, monkeypatch):
    m=Manager(tmp_path,MockBackend(),False)
    m.backend.simulated=False;m.auto_camera_requested=True;m.active=True
    monkeypatch.setattr('lab.devices.resolve',lambda *args:(_ for _ in ()).throw(AssertionError('Must not enumerate during capture')))
    m.retry_camera_if_idle({'ready':False})
    assert not m.camera_reconnecting
    m.active=False;m.close()
