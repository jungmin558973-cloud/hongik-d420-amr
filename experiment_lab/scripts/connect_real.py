"""Interactive local SSH setup. Password stays in memory; never enters chat/logs.

Installs only the ROS recording sidecar. Does not change the motor owner/service.
The authenticated SSH connection forwards the recording API while this window stays open.
"""
import json
import os
from pathlib import Path
import posixpath
import queue
import re
import secrets
import select
import shlex
import socket
import socketserver
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk,messagebox
import webbrowser
import keyring
import paramiko

ROOT=Path(__file__).resolve().parents[1]
STATE=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'HongikCafe'
STATUS=STATE/'experiment-lab-status.json'


def config():
    values={}
    for p in (ROOT/'connection.json',STATE/'connection.json'):
        if p.exists():values.update(json.loads(p.read_text(encoding='utf-8-sig')))
    ssh=(STATE/'ssh_config').read_text(encoding='utf-8-sig')
    def setting(name,default=''):
        m=re.search(r'^\s*'+name+r'\s+(.+?)\s*$',ssh,re.M|re.I)
        return m.group(1).strip('"') if m else default
    return values['jetson_address'],setting('User','seo'),setting('UserKnownHostsFile'),setting('HostKeyAlias')


class Tunnel(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True


def forward(transport,port,remote_port=None):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            channel=None
            try:
                active_transport=transport() if callable(transport) else transport
                channel=active_transport.open_channel('direct-tcpip',('127.0.0.1',remote_port or port),self.client_address,timeout=5)
                while channel and active_transport.is_active():
                    ready,_,_=select.select([self.request,channel],[],[],1)
                    if self.request in ready:
                        b=self.request.recv(65536)
                        if not b:break
                        channel.sendall(b)
                    if channel in ready:
                        b=channel.recv(65536)
                        if not b:break
                        self.request.sendall(b)
            except (OSError,EOFError,paramiko.SSHException):pass
            finally:
                if channel:channel.close()
    server=Tunnel(('127.0.0.1',port),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start();return server


class Connector:
    def __init__(self):
        self.window=tk.Tk();self.window.title('HONGIK CAFE - Real Jetson Setup');self.window.geometry('660x440')
        self.window.configure(bg='#f4f1e8');self.messages=queue.Queue();self.ssh=None;self.tunnels=[];self.child=None
        host,user,known,alias=config();self.known=known;self.alias=alias
        ttk.Label(self.window,text='Jetson actual recording setup',font=('Segoe UI',19,'bold')).pack(pady=18)
        ttk.Label(self.window,text='Backs up and extends the single motor bridge. No ARM/START sent.').pack()
        form=ttk.Frame(self.window);form.pack(fill='x',padx=40,pady=14)
        self.host=tk.StringVar(value=host);self.user=tk.StringVar(value=user);self.password=tk.StringVar()
        for row,(name,var) in enumerate((('Jetson address',self.host),('SSH user',self.user),('SSH password',self.password))):
            ttk.Label(form,text=name).grid(row=row,column=0,sticky='w',padx=5,pady=7)
            entry=ttk.Entry(form,textvariable=var,show='*' if name=='SSH password' else '',width=40)
            entry.grid(row=row,column=1,padx=5,pady=7)
            if name=='SSH password':entry.focus_set()
        self.button=ttk.Button(self.window,text='Connect / Install guarded control / Open camera',command=self.begin);self.button.pack(pady=10)
        self.label=ttk.Label(self.window,text='Enter the Jetson account password here, never in chat.',wraplength=580);self.label.pack(pady=12)
        ttk.Label(self.window,text='Keep this window open to keep the SSH tunnel connected.').pack()
        self.window.bind('<Return>',lambda e:self.begin());self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.window.after(100,self.poll)
        self._password=None;self._host=host;self._user=user;self.ssh_lock=threading.RLock()
        from connection_admin import start
        self.admin=start(self)
        self.write_status('WAITING_FOR_LOCAL_SSH_LOGIN')
        self.window.after(150,self.show_login)

    def show_login(self):
        self.window.deiconify();self.window.lift();self.window.attributes('-topmost',True)
        self.window.after(2000,lambda:self.window.attributes('-topmost',False))

    def ensure_ssh(self):
        with self.ssh_lock:
            if self.ssh and self.ssh.get_transport() and self.ssh.get_transport().is_active():return
            sock=socket.create_connection((self._host,22),timeout=5)
            client=paramiko.SSHClient();client.load_host_keys(self.known);client.set_missing_host_key_policy(paramiko.RejectPolicy())
            client.connect(self.alias,sock=sock,username=self._user,password=self._password or None,allow_agent=True,look_for_keys=True,timeout=8,auth_timeout=10)
            client.get_transport().set_keepalive(3);self.ssh=client

    def active_transport(self):
        self.ensure_ssh();return self.ssh.get_transport()

    def write_status(self,state,**data):
        STATE.mkdir(exist_ok=True)
        STATUS.write_text(json.dumps({'state':state,'utc':time.time(),**data},ensure_ascii=False,indent=2),encoding='utf-8')

    def progress(self,state,text):self.messages.put(text);self.write_status(state)

    def begin(self):
        if str(self.button['state'])=='disabled':return
        host=self.host.get().strip();user=self.user.get().strip();password=self.password.get()
        self._host=host;self._user=user;self._password=password or self._password
        self.password.set('');self.button['state']='disabled'
        threading.Thread(target=self.setup,args=(host,user,password),daemon=True).start()

    def run(self,script,password=None,timeout=240):
        stdin,stdout,stderr=self.ssh.exec_command('bash -lc '+shlex.quote(script),timeout=timeout)
        if password is not None:stdin.write(password+'\n');stdin.flush()
        stdin.channel.shutdown_write()
        # Drain both streams to avoid SSH window deadlock. Never display command/output secrets.
        channel=stdout.channel;started=time.monotonic();out=bytearray();err=bytearray()
        while not channel.exit_status_ready():
            if channel.recv_ready():out.extend(channel.recv(65536))
            if channel.recv_stderr_ready():err.extend(channel.recv_stderr(65536))
            if time.monotonic()-started>timeout:channel.close();raise TimeoutError('Remote setup timed out')
            time.sleep(.03)
        while channel.recv_ready():out.extend(channel.recv(65536))
        while channel.recv_stderr_ready():err.extend(channel.recv_stderr(65536))
        code=channel.recv_exit_status()
        if code:
            detail=(err or out).decode('utf-8',errors='replace')[-3000:] or ('Remote command exit '+str(code)+'; no diagnostic output')
            detail=re.sub(r'(Bearer\s+)[A-Za-z0-9_-]+',r'\1[REDACTED]',detail)
            (STATE/'experiment-setup-error.log').write_text(detail,encoding='utf-8')
            raise RuntimeError('Remote setup failed: '+detail[-500:])
        return out.decode('utf-8',errors='replace').strip()

    def setup(self,host,user,password):
        try:
            self.progress('CONNECTING','Connecting to trusted Jetson host...')
            if self.ssh and self.ssh.get_transport() and self.ssh.get_transport().is_active():client=self.ssh
            else:
                sock=socket.create_connection((host,22),timeout=5)
                client=paramiko.SSHClient();client.load_host_keys(self.known);client.set_missing_host_key_policy(paramiko.RejectPolicy())
                client.connect(self.alias,sock=sock,username=user,password=password or None,allow_agent=True,look_for_keys=True,timeout=8,auth_timeout=10)
            self.ssh=client;client.get_transport().set_keepalive(3)
            self.progress('SSH_CONNECTED','SSH connected. Inspecting ROS dependencies; motor services unchanged...')
            home=self.run('printf "%s" "$HOME"');remote=posixpath.join(home,'hongik_d420_experiment_lab')
            # Existing recorder on this port must be handled explicitly; never kill another service.
            busy=self.run("python3 - <<'PY'\nimport socket\ns=socket.socket();print(s.connect_ex(('127.0.0.1',18866))==0);s.close()\nPY")
            if busy=='True':
                # Reuse only our authenticated, isolated recorder; never kill another service.
                sftp=client.open_sftp()
                private=home+'/.config/hongik_d420_lab'
                # The original nohup shell PID can be stale; identify the actual
                # isolated Python agent by both argv AND canonical working dir.
                pid=self.run("python3 - <<'PID'\nfrom pathlib import Path\nroot=Path.home()/'hongik_d420_experiment_lab'\nfound=[]\nfor p in Path('/proc').iterdir():\n if not p.name.isdigit():continue\n try:\n  args=(p/'cmdline').read_bytes().split(b'\\0')\n  if b'lab.agent' in args and (p/'cwd').resolve()==root.resolve():found.append(p.name)\n except OSError:pass\nif len(found)!=1:raise SystemExit('Expected exactly one isolated recorder; found '+str(len(found)))\nprint(found[0])\nPID")
                if not pid.isdigit():raise RuntimeError('Unrecognised recorder process')
                identity=self.run('readlink -f /proc/'+pid+'/cwd')
                if identity!=remote:raise RuntimeError('Port 18866 belongs to another service')
                with sftp.open(private+'/agent.pid','w') as f:f.write(pid+'\n')
                with sftp.open(private+'/agent.env') as f:text=f.read().decode()
                sftp.close()
                match=re.fullmatch(r'D420_AGENT_TOKEN=([A-Za-z0-9_-]{32,})\n?',text)
                if not match:raise RuntimeError('Invalid private recorder credential file')
                token=match.group(1);keyring.set_password('HONGIK_CAFE_D420','agent_token',token)
                self.progress('UPDATING_RECORDER','Updating isolated recorder compatibility; existing motor services unchanged...')
                import signal
                # Check the authenticated recorder is idle before replacing only our sidecar.
                check_script="""import json,urllib.request,os,signal,time
from pathlib import Path
token=Path.home().joinpath('.config/hongik_d420_lab/agent.env').read_text().split('=',1)[1].strip()
req=urllib.request.Request('http://127.0.0.1:18866/status',headers={'Authorization':'Bearer '+token})
state=json.load(urllib.request.urlopen(req,timeout=3))
if state.get('recording'):raise SystemExit('Recorder is active; finish recording first')
pid=int(Path.home().joinpath('.config/hongik_d420_lab/agent.pid').read_text())
os.kill(pid,signal.SIGTERM)
time.sleep(2)
p=Path('/proc')/str(pid)
if p.exists():
 if b'lab.agent' not in (p/'cmdline').read_bytes().split(b'\\0') or (p/'cwd').resolve()!=Path.home()/'hongik_d420_experiment_lab':raise SystemExit('Recorder identity changed')
 os.kill(pid,signal.SIGKILL)
"""
                self.run("python3 - <<'CHECK'\n"+check_script+"\nCHECK")
                sftp=client.open_sftp()
                for module in (ROOT/'lab').glob('*.py'):sftp.put(str(module),remote+'/lab/'+module.name)
                try:sftp.mkdir(remote+'/scripts')
                except OSError:pass
                sftp.put(str(ROOT/'scripts/install_control_remote.py'),remote+'/scripts/install_control_remote.py');sftp.put(str(ROOT/'scripts/maintenance_gateway.py'),remote+'/scripts/maintenance_gateway.py');sftp.close()
                self.run('sleep 1; source /opt/ros/humble/setup.bash; set -a; . '+shlex.quote(private+'/agent.env')+'; set +a; export ROS_DOMAIN_ID=120; cd '+shlex.quote(remote)+'; nohup /usr/bin/python3 -m lab.agent --root '+shlex.quote(home+'/hongik_experiment_data')+' > '+shlex.quote(home+'/hongik_experiment_data/agent.log')+' 2>&1 < /dev/null & echo $! > '+shlex.quote(private+'/agent.pid'))
                self.progress('INSTALLING_CONTROL_OWNER','Backing up and extending the existing single motor owner; no drive goal...')
                try:self.run('source /opt/ros/humble/setup.bash; cd '+shlex.quote(remote)+'; /usr/bin/python3 scripts/install_control_remote.py',timeout=100)
                except RuntimeError as e:self.control_warning=str(e)
                password=None;self.complete_connection(client,token,host);return
            # Only install if missing. sudo consumes the password over stdin, never argv/log.
            try:self.run('source /opt/ros/humble/setup.bash; /usr/bin/python3 -c "import flask,rclpy,rosbag2_py,rosidl_runtime_py"',timeout=20)
            except RuntimeError:
                self.progress('INSTALLING_DEPENDENCIES','Installing missing ROS recording/Flask dependencies (sudo)...')
                self.run("sudo -S -p '' apt-get install -y python3-flask ros-humble-rosbag2-py ros-humble-rosidl-runtime-py",password=password,timeout=300)
            password=None
            self.progress('UPLOADING','Copying isolated recorder modules; no serial port is opened...')
            sftp=client.open_sftp()
            def mkdir(path,mode=0o755):
                try:sftp.stat(path)
                except OSError:sftp.mkdir(path,mode)
            mkdir(remote);mkdir(remote+'/lab')
            for p in (ROOT/'lab').glob('*.py'):sftp.put(str(p),remote+'/lab/'+p.name)
            conf=home+'/.config';mkdir(conf);private=conf+'/hongik_d420_lab';mkdir(private,0o700)
            token=secrets.token_urlsafe(40);envpath=private+'/agent.env'
            # Create with restrictive permissions before writing secret bytes.
            with sftp.open(envpath,'w') as f:sftp.chmod(envpath,0o600);f.write('D420_AGENT_TOKEN='+token+'\n')
            sftp.close();keyring.set_password('HONGIK_CAFE_D420','agent_token',token)
            data=home+'/hongik_experiment_data'
            script='\n'.join([
                'set -e','source /opt/ros/humble/setup.bash','set -a','. '+shlex.quote(envpath),'set +a',
                'export ROS_DOMAIN_ID=120','mkdir -p '+shlex.quote(data),'cd '+shlex.quote(remote),
                'nohup /usr/bin/python3 -m lab.agent --root '+shlex.quote(data)+' > '+shlex.quote(data+'/agent.log')+' 2>&1 < /dev/null &',
                'echo $! > '+shlex.quote(private+'/agent.pid')])
            self.run(script,timeout=20)
            try:
                sftp=client.open_sftp()
                try:sftp.mkdir(remote+'/scripts')
                except OSError:pass
                sftp.put(str(ROOT/'scripts/install_control_remote.py'),remote+'/scripts/install_control_remote.py');sftp.put(str(ROOT/'scripts/maintenance_gateway.py'),remote+'/scripts/maintenance_gateway.py');sftp.close()
                self.run('source /opt/ros/humble/setup.bash; cd '+shlex.quote(remote)+'; /usr/bin/python3 scripts/install_control_remote.py',timeout=100)
            except RuntimeError as e:self.control_warning=str(e)
            self.complete_connection(client,token,host)
        except Exception as exc:
            password=None
            self.write_status('ERROR',error_type=type(exc).__name__,detail=str(exc) if isinstance(exc,RuntimeError) else 'SSH authentication/network/host verification failed')
            self.messages.put('ERROR: '+(str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__+' - check password, network or trusted host key'))
            self.messages.put('__RETRY__')

    def complete_connection(self,client,token,host):
        self.progress('STARTING_TUNNEL','Recorder started. Opening SSH tunnel and verifying raw ROS sources...')
        self.tunnels.append(forward(self.active_transport,18866))
        import requests
        session=requests.Session();session.trust_env=False
        session.headers['Authorization']='Bearer '+token
        deadline=time.monotonic()+20;state=None
        while time.monotonic()<deadline:
            try:
                r=session.get('http://127.0.0.1:18866/status',timeout=2)
                if r.ok:state=r.json();break
            except requests.RequestException:pass
            time.sleep(.5)
        if state is None:raise RuntimeError('Recorder health check failed; inspect ~/hongik_experiment_data/agent.log')
        try:
            with socket.create_connection(('127.0.0.1',18765),timeout=.5):pass
        except OSError:
            try:self.tunnels.append(forward(self.active_transport,18765,8765))
            except OSError:pass
        # Reuse an already running real lab instead of rejecting its listening port.
        existing=False
        try:
            check=session.get('http://127.0.0.1:18865/api/session',timeout=1)
            if check.ok and check.json().get('real'):existing=True
            elif check.ok:raise RuntimeError('Close the MOCK server before real connection')
        except requests.RequestException:pass
        if not existing:
            env=os.environ.copy();env['D420_AGENT_TOKEN']=token
            self.child=subprocess.Popen([sys.executable,'-m','lab.server','--real','--auto-camera'],cwd=ROOT,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        token=None
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            try:
                response=session.get('http://127.0.0.1:18865/api/session',timeout=1)
                if response.ok:break
            except requests.RequestException:pass
            time.sleep(.3)
        sources=len(state.get('raw_topics',[]));self.write_status('READY_REAL_RECORDING',host=host,raw_topics=sources,odom_age=state.get('odom_age'),control_available=state.get('control_available'),control_warning=getattr(self,'control_warning',''))
        self.messages.put('Connected. Real camera + Jetson recording ready. Select record-only; automatic driving requires separate commissioning.')
        webbrowser.open('http://127.0.0.1:18865/experiment-lab')

    def poll(self):
        try:
            while True:
                msg=self.messages.get_nowait()
                if msg=='__RETRY__':self.button['state']='normal'
                else:self.label['text']=msg
        except queue.Empty:pass
        self.window.after(100,self.poll)

    def close(self):
        self._password=None
        if getattr(self,'admin',None):self.admin.shutdown();self.admin.server_close()
        if self.child and self.child.poll() is None:
            # Parent window/tunnel can close, but Jetson watchdog must remain independent.
            try:
                import requests
                s=requests.Session();s.trust_env=False
                token=s.get('http://127.0.0.1:18865/api/session',timeout=1).json()['token']
                s.post('http://127.0.0.1:18865/api/stop',json={},headers={'X-Lab-Token':token},timeout=3)
            except Exception:pass
            self.child.terminate()
        for server in self.tunnels:server.shutdown();server.server_close()
        if self.ssh:self.ssh.close()
        self.write_status('DISCONNECTED');self.window.destroy()


if __name__=='__main__':
    try:Connector().window.mainloop()
    except Exception as e:
        messagebox.showerror('HONGIK CAFE setup',type(e).__name__+': check existing HongikCafe SSH configuration')
