"""Windows OpenSSH transport; dedicated key permits only the two local APIs."""
from pathlib import Path
import json,os,socket,subprocess,time
from connect_real import config,ROOT,STATE


def main():
    key=STATE/'d420_forward_ed25519'
    if not key.is_file():raise RuntimeError('Connect once through the local setup window first')
    host,user,known,alias=config()
    pidfile=STATE/'native-forward.pid'
    if pidfile.exists():
        import psutil
        try:
            p=psutil.Process(int(pidfile.read_text()));args=p.cmdline()
            if p.name().lower()=='ssh.exe' and str(key) in args and '18876:127.0.0.1:18866' in args:
                try:
                    with socket.create_connection(('127.0.0.1',18876),timeout=.2):return p.pid
                except OSError:
                    raise RuntimeError('SSH is still connecting; Jetson forwarding is not ready')
        except (psutil.Error,ValueError):pass
    # Only prepare permissions when creating a tunnel. Running icacls every
    # watchdog tick flashes a console when the parent is pythonw.exe.
    identity=os.environ['USERDOMAIN']+'\\'+os.environ['USERNAME']
    subprocess.run(['icacls',str(key),'/inheritance:r','/grant:r',identity+':F'],capture_output=True,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    ssh=Path(os.environ['WINDIR'])/'System32/OpenSSH/ssh.exe'
    args=[str(ssh),'-N','-T','-i',str(key),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
          '-o','UserKnownHostsFile='+known,'-o','HostKeyAlias='+alias,'-o','ExitOnForwardFailure=yes',
          '-o','ConnectTimeout=5','-o','ConnectionAttempts=1',
          '-o','ServerAliveInterval=2','-o','ServerAliveCountMax=3','-o','TCPKeepAlive=yes',
          '-L','18876:127.0.0.1:18866','-L','18776:127.0.0.1:8765',user+'@'+host]
    with (STATE/'native-forward.log').open('ab') as log:
        process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    pidfile.write_text(str(process.pid))
    for _ in range(40):
        if process.poll() is not None:raise RuntimeError('Native SSH exited; inspect native-forward.log')
        try:
            with socket.create_connection(('127.0.0.1',18876),timeout=.2):break
        except OSError:time.sleep(.1)
    else:raise RuntimeError('Native forwarding listener not ready')
    connection=ROOT/'connection.json';d=json.loads(connection.read_text());d.update(local_port=18776,agent_url='http://127.0.0.1:18876',native_forward=True)
    connection.write_text(json.dumps(d,indent=2));return process.pid


if __name__=='__main__':print('Native OpenSSH forwarding PID',main())
