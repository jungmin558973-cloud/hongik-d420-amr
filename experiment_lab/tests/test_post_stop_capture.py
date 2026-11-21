from types import SimpleNamespace
from lab.runtime import Manager
from lab.backend import MockBackend


def test_quality_warning_keeps_recording_until_ten_seconds_after_confirmed_stop(tmp_path,monkeypatch):
    m=Manager(tmp_path,MockBackend(),False);clock=[0.]
    monkeypatch.setattr('lab.runtime.time.perf_counter',lambda:clock[0])
    monkeypatch.setattr('lab.runtime.time.time',lambda:100+clock[0])
    m.run_id='run';rows=[];finished=[]
    m.journal=SimpleNamespace(append=lambda file,row:rows.append(row))
    m.finish=lambda status:finished.append(status)
    m.robot={'odom_age':.01,'checks':{'live':True},'v':0.,'x':1.53,'y':0.,'events':[{'event':'RECORDING','run_id':'run','origin':[0,0,0]}]}
    m.preserve_quality_tail();clock[0]=.41;m.preserve_quality_tail()
    assert rows[0]['event']=='STOP_CONFIRMED' and rows[0]['x']==1.53
    clock[0]=10.40;m.preserve_quality_tail();assert not finished
    clock[0]=10.42;m.preserve_quality_tail();assert finished==['COMPLETE']
    m.close()
