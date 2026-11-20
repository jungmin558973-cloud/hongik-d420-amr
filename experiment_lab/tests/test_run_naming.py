from dataclasses import asdict
from lab.core import Config
from lab.storage import Store


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
