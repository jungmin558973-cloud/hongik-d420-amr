from lab.server import create_app
from lab.runtime import Manager
from lab.backend import MockBackend


def test_preflight_explains_4k_and_camera_blockers_without_arming(tmp_path):
    m=Manager(tmp_path,MockBackend(),False);app=create_app(m);client=app.test_client()
    token=client.get('/api/session').json['token']
    r=client.post('/api/preflight',headers={'X-Lab-Token':token},json={'require_4k':True})
    assert r.status_code==200 and not r.json['ready']
    assert any('4K' in x for x in r.json['blockers'])
    assert m.state=='READY' and not m.backend.control.active
    m.close()
