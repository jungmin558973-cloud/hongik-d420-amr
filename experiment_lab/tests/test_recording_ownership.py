import time
import requests
from types import SimpleNamespace
import pytest
from dataclasses import asdict
from lab.agent import create_agent
from lab.backend import RemoteBackend
from lab.core import Config
from lab.storage import Store


class Collector:
    def __init__(self):
        self.status={};self.latest={'odom_utc':time.time()};self.error='';self.recorder=None;self.inventory=[];self.starts=self.stops=0
    def start_record(self,path):self.starts+=1;self.recorder=SimpleNamespace(error='')
    def stop_record(self):self.stops+=1;self.recorder=None
    def request(self,*args,**kwargs):return {'ok':True}


def test_record_start_ack_is_idempotent_and_stop_cannot_close_new_run(tmp_path):
    ros=Collector();client=create_agent(tmp_path,'x'*40,ros).test_client();headers={'Authorization':'Bearer '+'x'*40}
    a,b='RUN_unique_recording_00001','RUN_unique_recording_00002'
    def post(route,rid):return client.post(route,json={'run_id':rid},headers=headers)
    assert post('/record/start',a).status_code==200
    assert post('/record/start',a).json['reused'] and ros.starts==1
    assert post('/record/start',b).status_code==400
    assert post('/record/stop',b).status_code==400 and ros.stops==0
    assert post('/record/stop',a).status_code==200
    assert post('/record/stop',a).status_code==200 and ros.stops==1
    assert post('/record/start',a).status_code==400 # Closed recordings are immutable.
    assert post('/record/start',b).status_code==200
    assert post('/record/stop',a).status_code==400 and ros.stops==1


def test_only_known_closed_stopped_recording_is_recovered(tmp_path,monkeypatch):
    store=Store(tmp_path);rid,path=store.begin(asdict(Config()))
    with store.connect() as db:db.execute("UPDATE experiment_index SET status='ABORTED' WHERE run_id=?",(rid,))
    state={'recording':True,'recording_run_id':rid,'state':'ABORTED','v':0.,'odom_age':.02}
    b=RemoteBackend('http://127.0.0.1:18876','x'*40);stopped=[];downloaded=[];finalized=[]
    b.call=lambda route:state
    b.stop_record=lambda recording_id:stopped.append(recording_id)
    b.download=lambda recording_id,p:downloaded.append(recording_id)
    monkeypatch.setattr(store,'finalize',lambda *args:finalized.append(args))
    b.recover_closed_recording(store)
    assert stopped==downloaded==[rid] and finalized[0][1]=='ABORTED'
    state['recording_run_id']='RUN_other_person_recording';stopped.clear()
    with pytest.raises(ValueError):b.recover_closed_recording(store)
    assert not stopped
    state['recording_run_id']=rid;state['v']=.1
    with pytest.raises(ValueError):b.recover_closed_recording(store)
    assert not stopped


def test_uncertain_start_accepts_only_same_run_without_resending_motion():
    b=RemoteBackend('http://127.0.0.1:18876','x'*40);calls=[]
    state={'run_id':'same','recording_run_id':'same','recording':True,'state':'MOVING'}
    def call(route,data=None):
        calls.append(route)
        if route=='/control/start':raise requests.ReadTimeout()
        return state
    b.call=call;b.start(Config(s_curve=False),'same','arm')
    assert calls==['/control/start','/status'] and b.control_run_id=='same'
    b.status={};state['run_id']='different';calls.clear()
    with pytest.raises(requests.ReadTimeout):b.start(Config(s_curve=False),'same','arm')
    assert calls.count('/control/start')==1
