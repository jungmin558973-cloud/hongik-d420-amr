"""Keep forwarding alive across Wi-Fi reconnects; never issues robot commands."""
import json,msvcrt,os,time
from connect_real import STATE
from start_native_tunnel import main


def run():
    STATE.mkdir(exist_ok=True)
    with (STATE/'native-watchdog.lock').open('a+b') as lock:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:return
        (STATE/'native-watchdog.pid').write_text(str(os.getpid()))
        while True:
            try:
                pid=main();status={'state':'FORWARDING','pid':pid,'utc':time.time()}
            except Exception as e:status={'state':'RECONNECTING','error_type':type(e).__name__,'utc':time.time()}
            (STATE/'native-forward-status.json').write_text(json.dumps(status))
            time.sleep(1)


if __name__=='__main__':run()
