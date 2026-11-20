from lab.core import Config,Controller,Observation,Validation


def controller():
    c=Controller(Validation());o=Observation()
    checks=dict.fromkeys(('space_clear','physical_stop_ready','cup_secure','origin_checked','operator_present'),True)
    c.start(Config(commissioning=True,s_curve=False),'speed-test',c.arm(0,checks),.01,o)
    return c,o


def test_one_encoder_spike_does_not_abort_but_sustained_overspeed_does():
    c,o=controller();o.v=.246
    c.beat(1,.06);c.tick(.06,o)
    assert c.active
    o.v=.2;c.beat(2,.11);c.tick(.11,o)
    assert c.active
    o.v=.26
    for seq in range(3,9):
        now=seq*.05;c.beat(seq,now);c.tick(now,o)
    assert c.state=='ABORTED' and 'sustained' in c.reason


def test_large_overspeed_still_stops_immediately():
    c,o=controller();o.v=.4;c.beat(1,.06);c.tick(.06,o)
    assert c.state=='ABORTED' and c.command==0


def test_bounded_capture_tolerates_short_wifi_gap_but_stops_on_loss():
    c,o=controller()
    for i in range(1,35):c.tick(.01+i*.05,o)
    assert c.active
    for i in range(35,43):c.tick(.01+i*.05,o)
    assert c.state=='ABORTED' and 'heartbeat' in c.reason


def test_network_heartbeat_does_not_cut_already_stopped_observation():
    c,o=controller();c.state='SETTLING';c.phase_at=.01
    for i in range(1,100):c.tick(.01+i*.05,o)
    assert c.state=='SETTLING' and c.command==0
