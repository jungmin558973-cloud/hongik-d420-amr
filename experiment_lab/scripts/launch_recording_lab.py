"""Reopen the installed lab without re-entering the forwarding password."""
import os,subprocess,sys,time,webbrowser
import requests
from connect_real import ROOT,STATE


def main():
    if not (STATE/'d420_forward_ed25519').exists():
        from connect_real import Connector
        Connector().window.mainloop();return
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    subprocess.Popen([sys.executable,str(ROOT/'scripts/native_tunnel_watchdog.py')],cwd=ROOT,creationflags=flags)
    subprocess.Popen([sys.executable,str(ROOT/'scripts/ensure_curve_support.py')],cwd=ROOT,creationflags=flags)
    s=requests.Session();s.trust_env=False
    running=False
    try:
        r=s.get('http://127.0.0.1:18865/api/session',timeout=1)
        if r.ok:
            if not r.json().get('real'):raise RuntimeError('Close the MOCK server on port 18865 first')
            running=True
    except requests.RequestException:pass
    if not running:
        subprocess.Popen([sys.executable,'-m','lab.server','--real','--auto-camera'],cwd=ROOT,creationflags=flags)
    for _ in range(60):
        try:
            if s.get('http://127.0.0.1:18865/api/session',timeout=.5).ok:break
        except requests.RequestException:pass
        time.sleep(.2)
    try:
        session=s.get('http://127.0.0.1:18865/api/session',timeout=2).json()
        status=s.get('http://127.0.0.1:18865/api/status',timeout=3).json()
        camera=status.get('camera',{})
        if not status.get('active') and not status.get('saving') and (not camera.get('ready') or 'C920' not in camera.get('device_name','').upper()):
            s.post('http://127.0.0.1:18865/api/camera',headers={'X-Lab-Token':session['token']},json={'source':'auto','backend':'DSHOW','width':1920,'height':1080,'fps':30},timeout=12)
    except requests.RequestException:pass
    webbrowser.open('http://127.0.0.1:18865/experiment-lab?build=160cm-10sec-settings')


if __name__=='__main__':main()
