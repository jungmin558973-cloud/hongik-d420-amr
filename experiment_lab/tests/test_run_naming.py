from dataclasses import asdict
from lab.core import Config
from lab.storage import Store
from types import SimpleNamespace


def test_condition_folders_repeat_persist_and_do_not_overwrite(tmp_path):
    c=asdict(Config(s_curve=False));s=Store(tmp_path)
    first,p1=s.begin(c);second,p2=Store(tmp_path).begin(c)
    assert p1.name.endswith('_촬영중') and p2.name.endswith('_촬영중') and p1!=p2 and p1.parent==p2.parent
    assert '회차' not in p1.name and 'L1.60m_V20cmps_SOFF_TOFF' in p1.name
    assert (p1/'실험설명서.md').exists()
    assert p1.parent.name=='A_1.6m_20cmps'
    assert first!=second and s.path(first)==p1
    _,p3=s.begin({**c,'speed':.3});assert p3.name.endswith('_촬영중') and p3.parent!=p1.parent
    _,p4=s.begin({**c,'acceleration':.3});assert '_a0.3_' in p4.name and p4!=p1
    _,mock=s.begin(c,True);assert mock.parent.name.startswith('MOCK_')
    assert s.list()[0]['label']['repeat']==1


def test_same_millisecond_and_short_id_collision_never_overwrites(tmp_path,monkeypatch):
    monkeypatch.setattr('lab.storage.time.time',lambda:1700000000.123)
    counter=iter(range(1,20))
    monkeypatch.setattr('lab.storage.uuid.uuid4',lambda:SimpleNamespace(hex='a'*10+f'{next(counter):022x}'))
    s=Store(tmp_path);a,pa=s.begin(asdict(Config()));original=(pa/'manifest.json').read_bytes()
    b,pb=s.begin(asdict(Config()))
    assert a!=b and pa!=pb and len(a.split('_')[-1])==32
    assert (pa/'manifest.json').read_bytes()==original
    assert '_n2_' in pb.name


def test_final_folder_collision_preserves_existing_folder(tmp_path):
    s=Store(tmp_path);rid,path=s.begin(asdict(Config()))
    stem=path.name.removesuffix('_촬영중');occupied=path.with_name(stem+'_실패');occupied.mkdir()
    (occupied/'keep.txt').write_text('previous result')
    saved=s.organize(rid,path,{'folder_status':'실패'})
    assert saved!=occupied and saved.exists() and (occupied/'keep.txt').read_text()=='previous result'
