import pytest
from lab.backend import MockBackend
from lab.runtime import Manager,CHECKS
from lab.server import create_app


@pytest.fixture
def api(tmp_path):
    m=Manager(tmp_path,MockBackend(),False);a=create_app(m);c=a.test_client()
    token=c.get('/api/session').json['token']
    yield c,{'X-Lab-Token':token},m
    m.close()


def test_api_csrf_gates_and_queue(api):
    c,h,m=api
    assert c.post('/api/arm',json={}).status_code==403
    assert c.post('/api/arm',headers={**h,'Origin':'https://evil.example'},json={}).status_code==403
    assert c.post('/api/start',headers=h,json={}).status_code==409
    assert c.post('/api/queue',headers=h,json={'repeats':3}).status_code==200
    assert len(c.get('/api/queue').json['rows'])==54
    c.post('/api/queue',headers=h,json={'repeats':3})
    assert len(c.get('/api/queue').json['rows'])==54
    assert c.post('/api/queue/start',headers=h,json={'key':'fake','confirmed':False}).status_code==409
    assert c.get('/api/recommend').json['status']=='INSUFFICIENT_DATA'
    assert c.get('/experiment-lab').status_code==200


def test_mock_connection_loss_aborts_and_camera_failure_server_survives(api):
    c,h,m=api
    m.active=True;m.record_only=False;m.backend.fault='network'
    # No run allocated: isolate emergency command delivery from artifact saving.
    calls=[];m.finish=lambda s:calls.append(s)
    m.tick();assert m.backend.control.command==0 and calls==['ABORTED']
    assert c.get('/api/status').status_code==200
    m.active=False
