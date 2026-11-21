import math
import pytest
from lab.core import Config,Validation,Profile,Controller,Observation,matrix,TrayPredictor

CHECKS=dict.fromkeys(('space_clear','physical_stop_ready','cup_secure','origin_checked','operator_present'),True)
VALID=Validation(.4,2.,True,('OFF','LEVEL','PREDICTIVE'),True,'mock')


@pytest.mark.parametrize('distance,speed',[(.05,.4),(.6,.12),(1.5,.2),(1.5,.4),(2.,.3),(2.,.4)])
@pytest.mark.parametrize('smooth',[True,False])
def test_profile_constraints(distance,speed,smooth):
    p=Profile(distance,speed,.2,.25,.5,smooth);dt=.001;last_v=0.;integral=0.
    for n in range(math.ceil(p.duration/dt)+1):
        x,v,a,j=p.at(n*dt)
        assert -.000001<=v<=speed+1e-8
        assert -.250001<=a<=.200001
        if smooth:assert abs(j)<=.500001
        integral+=(v+last_v)*.5*dt;last_v=v
    assert abs(integral-distance)<2e-5
    assert p.at(p.duration)==(distance,0.,0.,0.)


def run(c,inject=None):
    controller=Controller(VALID);o=Observation();token=controller.arm(0.,CHECKS)
    controller.start(c,'unique_run',token,.01,o);last_v=0.
    for n in range(1,12000):
        now=.01+n*.02;controller.beat(n,now)
        o.v+=(controller.command-o.v)*.2;o.x+=o.v*.02
        if inject and now>3:inject(controller,o,now)
        controller.tick(now,o)
        if not controller.active:return controller,o
    pytest.fail('did not finish')


def test_return_measured_distance_and_stop_events():
    c,o=run(Config(distance=.6,speed=.12,return_home=True))
    assert c.state=='COMPLETE';assert abs(o.x)<.025
    assert len([e for e in c.events if e['event']=='RETURNING'])==1
    assert len([e for e in c.events if e['event']=='STOP_CONFIRMED'])==2
    f=[e for e in c.events if e['event']=='STOP_CONFIRMED' and e['segment']=='forward'][0]
    assert abs(f['x']-.6)<.025


@pytest.mark.parametrize('fault',['live','sensors','clear_front','recorder_ok','tray_ok'])
def test_sensor_fault_bypasses_curve(fault):
    c,o=run(Config(distance=.6,speed=.12,tray='LEVEL'),lambda c,o,t:setattr(o,fault,False))
    assert c.state=='ABORTED' and c.command==0.


def test_watchdog_and_tick_latency():
    c=Controller(VALID);o=Observation();t=c.arm(0,CHECKS);c.start(Config(), 'watchdog',t,.01,o)
    assert c.tick(.7,o)==0 and c.state=='ABORTED'
    assert 'heartbeat' in c.reason


def test_arm_single_use_expiry_duplicate_and_limits():
    c=Controller();o=Observation();t=c.arm(0,CHECKS)
    with pytest.raises(ValueError):c.start(Config(),'unsafe',t,.1,o)
    c=Controller(VALID);t=c.arm(0,CHECKS)
    with pytest.raises(ValueError):c.start(Config(),'expired',t,31,o)
    c.start(Config(),'duplicate',t,.1,o);c.abort('test',.2)
    t=c.arm(.3,CHECKS)
    with pytest.raises(ValueError):c.start(Config(),'duplicate',t,.4,o)


def test_matrix_and_predictor_limit():
    m=matrix();assert len(m)==54 and len({r['key'] for r in m})==54
    assert len(matrix(1))==18
    assert all(r['status']=='PENDING_VALIDATION' for r in m)
    p=Profile(2,.4,.2,.25,.5);pred=TrayPredictor();c=Config()
    last=0.
    for n in range(1000):
        a=pred.step(c,p,n*.02,.02)
        assert abs(a)<=c.angle_limit;assert abs(a-last)<=c.angle_rate*.02+1e-9;last=a


@pytest.mark.parametrize('data',[{'speed':True},{'speed':float('nan')},{'distance':3},{'s_curve':1},{'return_home':'false'},{'jerk':0},{'tray':'invented'}])
def test_input_validation(data):
    with pytest.raises(ValueError):Config.parse(data)
