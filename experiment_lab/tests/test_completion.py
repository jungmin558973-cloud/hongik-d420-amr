from dataclasses import asdict
from lab.completion import assess
from lab.core import Config
from lab.storage import Store,dump


def test_success_requires_distance_and_ten_seconds_not_status_only():
    c=asdict(Config())
    events=[{'event':'STOP_CONFIRMED','segment':'forward','utc':100,'x':1.63}]
    assert assess('COMPLETE',c,[{'utc':110}],events)['success']
    assert not assess('COMPLETE',c,[{'utc':109}],events)['success']
    assert not assess('COMPLETE',c,[{'utc':110}],[])['success']
    assert not assess('ABORTED',c,[{'utc':110}],events)['success']


def test_completed_folder_label_and_next_attempt_do_not_overwrite(tmp_path):
    s=Store(tmp_path);c=asdict(Config(s_curve=False));rid,path=s.begin(c)
    new=s.organize(rid,path,{'folder_status':'성공','success':True})
    assert new.name.endswith('_성공') and '회차' not in new.name and new.exists() and s.path(rid)==new
    _,next_path=s.begin(c);assert next_path.name.endswith('_촬영중') and next_path!=new
