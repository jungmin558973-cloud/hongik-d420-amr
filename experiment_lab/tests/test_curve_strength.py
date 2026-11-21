import pytest
from dataclasses import asdict
from lab.core import Config,Profile,Controller,Observation,Validation
from lab.storage import Store


def test_endpoints_preserve_existing_motion_and_middle_changes_jerk():
    args=(1.6,.4,.2,.25,.5)
    off=Profile(*args,False);zero=Profile(*args,True,strength=0)
    full=Profile(*args,True);hundred=Profile(*args,True,strength=100)
    middle=Profile(*args,True,strength=50)
    assert off.segments==zero.segments and full.segments==hundred.segments
    assert off.acceleration_duration<middle.acceleration_duration<full.acceleration_duration
    assert middle.effective_jerk==1. and full.effective_jerk==.5
    for value in [-1,101,float('nan'),True]:
        with pytest.raises(ValueError):Config.parse({'s_curve_strength':value})
    assert not Config.parse({'s_curve_strength':0}).s_curve


@pytest.mark.parametrize('strength',[0,1,25,50,75,100])
@pytest.mark.parametrize('speed',[.2,.3,.4])
def test_strength_runs_to_distance_and_uses_one_abrupt_stop(strength,speed):
    cfg=Config.parse({'s_curve_strength':strength,'speed':speed,'commissioning':True,'obstacle_stop':False})
    ctl=Controller(Validation());o=Observation()
    checks=dict.fromkeys(('space_clear','physical_stop_ready','cup_secure','origin_checked','operator_present'),True)
    ctl.start(cfg,'strength',ctl.arm(0,checks),.01,o)
    for i in range(1,2200):
        now=.01+i*.02;ctl.beat(i,now);o.v+=(ctl.command-o.v)*.2;o.x+=o.v*.02;ctl.tick(now,o)
        if not ctl.active:break
    assert ctl.state=='COMPLETE'
    stops=[e for e in ctl.events if e['event']=='ZERO_COMMAND']
    assert len(stops)==1 and 1.6<=stops[0]['x']<1.61


def test_different_strengths_are_not_pooled_in_same_folder(tmp_path):
    s=Store(tmp_path)
    _,a=s.begin(asdict(Config(s_curve_strength=25)))
    _,b=s.begin(asdict(Config(s_curve_strength=75)))
    assert a.parent!=b.parent and '_SC25_' in a.name and '_SC75_' in b.name


def test_old_jetson_keeps_binary_modes_and_rejects_unsupported_strength():
    from lab.backend import RemoteBackend
    b=RemoteBackend('http://127.0.0.1:18876','x'*40);sent=[]
    b.call=lambda route,data:sent.append(data)
    b.start(Config(s_curve=False),'old','token')
    assert 's_curve_strength' not in sent[-1]['config']
    with pytest.raises(ValueError):b.start(Config(s_curve_strength=50),'middle','token')
    b.status['capabilities']={'s_curve_strength':True}
    b.start(Config(s_curve_strength=50),'updated','token')
    assert sent[-1]['config']['s_curve_strength']==50
