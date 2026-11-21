"""List identities without opening any port. Never probes devices with commands."""
from pathlib import Path
import json
from serial.tools import list_ports


def devices():
    aliases=list(Path('/dev/serial/by-id').glob('*'))
    result=[]
    for item in list_ports.comports():
        resolved=Path(item.device).resolve()
        links=[p for p in aliases if p.resolve()==resolved]
        port=str(links[0]) if links else item.device
        serial=item.serial_number or ''
        reserved=(item.vid is None or item.pid is None or
                  (item.vid,item.pid)==(0x1a86,0x7523) or
                  '4015c9a5201ef111a8bdc9e40f0f12f8' in serial.lower())
        result.append({'port':port,'device':str(resolved),'vid':item.vid,'pid':item.pid,
                       'serial':serial,'description':item.description or '',
                       'reserved':reserved,'native_esp':item.vid==0x303a})
    return result


def validate_port(port):
    matches=[d for d in devices() if d['port']==port]
    if len(matches)!=1 or matches[0]['reserved']:
        raise ValueError('ESP32 목록의 장치를 선택하세요. D420/C1 포트는 사용할 수 없습니다.')
    return matches[0]


if __name__=='__main__': print(json.dumps(devices(),ensure_ascii=False))
