import pytest
from lab.core import Config,Validation,Controller,Observation


def test_first_trial_is_explicit_bounded_and_does_not_modify_validation():
    v=Validation();c=Config(s_curve=False,commissioning=True)
    v.check(c);assert v.max_speed==.12 and v.max_distance==.6
    v.check(Config(s_curve=False,speed=.3,commissioning=True));v.check(Config(speed=.4,commissioning=True))
    for change in [{'speed':.5},{'distance':3},{'tray':'LEVEL'},{'return_home':True}]:
        with pytest.raises(ValueError):v.check(Config.parse({**vars(c),**change}))
    with pytest.raises(ValueError):v.check(Config(s_curve=False))
    ctl=Controller(v);checks=dict.fromkeys(('space_clear','physical_stop_ready','cup_secure','origin_checked','operator_present'),True)
    ctl.start(c,'commission',ctl.arm(0,checks),.01,Observation())
    assert ctl.run_speed_limit==.2 and v.max_speed==.12
    assert ctl.tick(.7,Observation())==0 and ctl.state=='ABORTED'
