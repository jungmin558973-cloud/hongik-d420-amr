from dataclasses import asdict
import json
import math
import pytest
from lab.sync import estimate,merge
from lab.analysis import metrics,recommend,compare
from lab.core import Config,Validation
from lab.storage import Store,Journal,sha
from lab.drive import UploadQueue
from lab.vision import Tracker,synthetic_frame,SYNTHETIC_ROI


def test_clock_offset_and_merge_gaps():
    probes=[(10+i,10+i+.105,10+i+.106,10+i+.011) for i in range(9)]
    c=estimate(probes);assert abs(c['jetson_minus_windows_s']-.1)<1e-8
    f=[{'utc':10.,'seq':0,'valid':True,'displacement_px':1},{'utc':10.5,'seq':1,'valid':False}]
    r=[{'utc':10.1,'v':.2}]
    rows=merge(f,r,c);assert rows[0]['v']==.2
    assert rows[-1]['v'] is None and rows[-1]['valid'] is False
    assert all(not x['interpolated'] for x in rows)


def test_vision_tracks_cup_rejects_loss_and_no_mm():
    tracker=Tracker(SYNTHETIC_ROI)
    for n in range(30):
        m,overlay=tracker.process(synthetic_frame(n/30),n/30)
        assert m['valid'];assert 'displacement_mm' not in m
    import numpy as np
    lost,_=tracker.process(np.full((360,640,3),220,dtype=np.uint8),2.)
    assert not lost['valid'] and lost['displacement_px'] is None
    unconfigured,_=Tracker().process(synthetic_frame(0));assert not unconfigured['valid']


def test_kpi_forward_scope_and_formula():
    c=asdict(Config());f=[{'utc':float(i),'valid':True,'displacement_px':x} for i,x in enumerate((1.,-1.,3.,-3.))]
    f.append({'utc':10.,'valid':True,'displacement_px':1000.,'segment':'return'})
    m=metrics(f,[],[],c)
    assert m['max_displacement_px']==3.;assert m['rms_px']==pytest.approx(math.sqrt(5.))
    assert m['peak_to_peak_px']==6.;assert m['settling_s'] is None
    assert recommend([{'simulated':True}],Validation())['status']=='INSUFFICIENT_DATA'


def test_crash_recovery_hash_and_upload_retry(tmp_path):
    store=Store(tmp_path);rid,path=store.begin(asdict(Config()),True);j=Journal(path)
    j.append('camera_frames.jsonl',{'utc':10.,'valid':False,'seq':0})
    with (path/'camera_frames.jsonl').open('a') as f:f.write('{partial')
    assert store.recover()==[rid]
    path=store.path(rid)
    assert store.list()[0]['status']=='ABORTED'
    manifest=json.loads((path/'manifest.json').read_text())
    assert all(sha(path/k)==v['sha256'] for k,v in manifest['files'].items())
    def fail():raise ConnectionError('secret must not leak')
    q=UploadQueue(store,fail);assert q.once(now=100)
    s=q.status()[0];assert s['state']=='RETRY' and s['attempts']==1
    assert 'secret' not in s['error'];assert (store.path(rid)/'report.html').exists()
    q.retry(rid);assert q.status()[0]['next_at']==0


def test_comparison_does_not_mix_sim_real_or_fill():
    def r(id,fill,sim):return {'run_id':id,'config':{**asdict(Config()),'fill_pct':fill},'metrics':{'actual_speed':{'max':.2},'actual_distance_m':1.5,'rms_px':2.,'valid_ratio':1.},'simulated':sim}
    assert len(compare([r('1',70,False),r('2',80,False),r('3',70,True)]))==3
