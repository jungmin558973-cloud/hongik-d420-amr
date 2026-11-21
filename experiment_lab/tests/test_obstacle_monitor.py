from lab.core import Controller,Validation,Config,Observation


def test_operator_monitor_mode_ignores_obstacles_but_keeps_distance_stop():
    c=Controller(Validation());o=Observation(sensors=False,clear_front=False)
    cfg=Config(speed=.3,s_curve=False,commissioning=True,obstacle_stop=False)
    checks=dict.fromkeys(('space_clear','physical_stop_ready','cup_secure','origin_checked','operator_present'),True)
    c.start(cfg,'monitor_test',c.arm(0,checks),.01,o)
    for n in range(1,2000):
        now=.01+n*.02;c.beat(n,now);o.v+=(c.command-o.v)*.2;o.x+=o.v*.02;c.tick(now,o)
        if not c.active:break
    assert c.state=='COMPLETE'
    stops=[e for e in c.events if e['event']=='ZERO_COMMAND']
    assert len(stops)==1 and 1.6<=stops[0]['x']<1.61
    assert all(e['run_id']=='monitor_test' for e in c.events)


def test_monitor_mode_still_stops_on_lost_odometry_and_heartbeat():
    for field in ['live','recorder_ok']:
        c=Controller();o=Observation(sensors=False,clear_front=False)
        checks=dict.fromkeys(('space_clear','physical_stop_ready','cup_secure','origin_checked','operator_present'),True)
        cfg=Config(s_curve=False,commissioning=True,obstacle_stop=False)
        c.start(cfg,'fail_'+field,c.arm(0,checks),.01,o)
        setattr(o,field,False);c.beat(1,.02)
        assert c.tick(.02,o)==0 and c.state=='ABORTED'
