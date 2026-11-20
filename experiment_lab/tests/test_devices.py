import pytest
from lab.devices import resolve


def test_prefer_c920_by_name_and_never_fall_back_to_laptop():
    inventory=[{'index':0,'name':'ASUS FHD webcam'},{'index':1,'name':'HD Pro Webcam C920'}]
    assert resolve('auto',inventory=inventory)['source']==1
    assert resolve(0,inventory=inventory)['device_name']=='ASUS FHD webcam'
    with pytest.raises(ValueError):resolve('auto',inventory=inventory[:1])
    assert not resolve(1,'MSMF',inventory)['identity_verified']
