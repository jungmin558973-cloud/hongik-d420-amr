from types import SimpleNamespace
import pytest
from lab.teleop import TeleopProxy
from lab.runtime import CHECKS


def test_teleop_ownership_sequence_and_selected_limits():
    m=SimpleNamespace(active=False,record_only=False);t=TeleopProxy('http://127.0.0.1:1',m);calls=[]
    t.get=lambda:{'active':False,'allow_real':True,'connected':True}
    t.call=lambda op,data:calls.append((op,data)) or {'state':{'control_id':'test'}}
    try:
        with pytest.raises(ValueError):t.arm({})
        arm=t.arm({'checks':dict.fromkeys(CHECKS,True)})
        with pytest.raises(ValueError):t.input({'client':arm['client'],'sequence':0,'speed':1.5,'yaw_speed':.2,'forward':1,'turn':0})
        d={'client':arm['client'],'sequence':1,'speed':.12,'yaw_speed':.2,'forward':1,'turn':0}
        t.input(d)
        with pytest.raises(ValueError):t.input(d)
        t.stop();assert not t.enabled and calls[-1][0]=='stop'
    finally:t.close()
