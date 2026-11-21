"""Apply this release to an idle Jetson when it reconnects; never start motion."""
import json
import time
import requests
import keyring
from connect_real import STATE,ROOT


def apply_support():
    session=requests.Session();session.trust_env=False
    token=keyring.get_password('HONGIK_CAFE_D420','agent_token')
    if not token:return
    session.headers['Authorization']='Bearer '+token
    connection=json.loads((ROOT/'connection.json').read_text(encoding='utf-8'))
    for _ in range(30):
        try:
            r=session.get(connection['agent_url']+'/status',timeout=(.5,1));r.raise_for_status();status=r.json()
            if status.get('capabilities',{}).get('s_curve_strength'):
                result={'state':'READY','s_curve_strength':True};break
            if status.get('recording') or status.get('state') in ('MOVING','STOPPING','BRAKING','SETTLING','POSTROLL','RETURNING','RECORDING') or status.get('v') is None or abs(status['v'])>.008:
                result={'state':'WAITING_FOR_IDLE'};break
            from deploy_jetson import deploy
            deploy();result={'state':'UPDATED','s_curve_strength':True};break
        except requests.RequestException:time.sleep(1)
        except Exception as e:
            result={'state':'UPDATE_FAILED','error':str(e)};break
    else:result={'state':'WAITING_FOR_JETSON'}
    result['utc']=time.time();(STATE/'curve-support-status.json').write_text(json.dumps(result),encoding='utf-8')


def main():
    import msvcrt
    STATE.mkdir(exist_ok=True)
    with (STATE/'curve-support.lock').open('a+b') as lock:
        lock.write(b'0');lock.flush();lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:return
        apply_support()


if __name__=='__main__':main()
