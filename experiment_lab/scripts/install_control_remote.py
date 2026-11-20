"""Jetson-only guarded installation into the existing single serial owner.
Never sends ARM/START. Backs up exact files and rolls back if restart fails.
"""
from pathlib import Path
import json,shutil,subprocess,time,urllib.request,os,signal,py_compile


def main():
    home=Path.home();root=Path(__file__).resolve().parents[1]
    install_forwarding_key(root,home)
    app=home/'d420_ws/scenario_lab';mapping=app/'jetson/mapping_bridge.py'
    # Python searches the executed script's directory as well as inserted paths.
    # Current deployment keeps the motor owner beside mapping_bridge.py.
    bridge=app/'jetson/serial_bridge.py'
    for p in (mapping,bridge):
        if not p.resolve().is_relative_to(home.resolve()) or not p.is_file():raise RuntimeError('Actual bridge path not verified: '+str(p))
    try:
        with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=3) as r:state=json.load(r)
    except Exception:
        print(json.dumps(connection_diagnostics(home,app)));return
    if state.get('active') or abs(state.get('v',999))>.005:raise RuntimeError('Stop chassis and existing control first')
    token=(home/'.config/hongik_d420_lab/agent.env').read_text().split('=',1)[1].strip()
    req=urllib.request.Request('http://127.0.0.1:18866/status',headers={'Authorization':'Bearer '+token})
    try:
        with urllib.request.urlopen(req,timeout=3) as r:record=json.load(r)
    except urllib.error.URLError as exc:
        import errno
        if getattr(exc.reason,'errno',None)!=errno.ECONNREFUSED:raise
        start_missing_recorder(root,home)
        with urllib.request.urlopen(req,timeout=3) as r:record=json.load(r)
    if record.get('recording'):
        print(json.dumps({'recording_active':True,'diagnostics':connection_diagnostics(home,app)}));return
    old_mapping=mapping.read_text();old_bridge=bridge.read_text()
    if 'D420_EXPERIMENT_OWNER_EXTENSION' in old_mapping:
        refresh_recorder(root,home)
        refresh_owner(root,home)
        print(json.dumps({'installed':True,'recorder_refreshed':True,'owner_refreshed':True,'forwarding_key_installed':True,'diagnostics':connection_diagnostics(home,app)}));return
    if 'local_route_factory=LocalRouteAdapter' not in old_mapping or 'def receive_cmd(self, msg):' not in old_bridge or 'local_route_factory=None' not in old_bridge:
        raise RuntimeError('Unknown existing source version; refused to overwrite')
    backup=home/'d420_ws/team_app_backups'/('experiment_owner_'+time.strftime('%Y%m%d_%H%M%S'))
    backup.mkdir(parents=True)
    shutil.copy2(mapping,backup/'mapping_bridge.py');shutil.copy2(bridge,backup/'serial_bridge.py')
    unit=subprocess.check_output(['systemctl','--user','cat','hongik-scenarios.service'],text=True)
    (backup/'service-before.txt').write_text(unit)
    patch='\n# D420_EXPERIMENT_OWNER_EXTENSION\nimport os\nsys.path.insert(0,'+repr(str(root))+')\nfrom lab.jetson_adapter import ExperimentAdapter\nos.environ["D420_EXPERIMENT_MOTION"]="1"\ndef experiment_factory(node):\n    return ExperimentAdapter(node,LocalRouteAdapter(node))\n'
    new_mapping=old_mapping.replace("if __name__=='__main__':",patch+"\nif __name__=='__main__':").replace('local_route_factory=LocalRouteAdapter','local_route_factory=experiment_factory')
    if '# D420_EXPERIMENT_OWNER_EXTENSION' not in new_mapping:raise RuntimeError('Unknown entry point formatting; source unchanged')
    new_bridge=old_bridge
    if "hasattr(self.local_route, 'external_command')" not in new_bridge:
        needle='            vx, wz = msg.linear.x, msg.angular.z\n'
        if needle not in new_bridge:raise RuntimeError('Cannot locate command hook')
        new_bridge=new_bridge.replace(needle,needle+'''            if self.local_route and hasattr(self.local_route, 'external_command'):
                if not self.local_route.external_command(vx, wz):
                    self.cmd_v = self.cmd_w = 0.0
                    self.last_cmd_time = 0.0
                    return
''')
    if 'self.max_linear_speed = max_linear_speed' not in new_bridge:
        new_bridge=new_bridge.replace('            self.last_tx_state = None','            self.last_tx_state = None\n            self.max_linear_speed = max_linear_speed')
    if "hasattr(self.local_route, 'motor_output')" not in new_bridge:
        new_bridge=new_bridge.replace('                    self.tx_count += 1','''                    self.tx_count += 1
                    if self.local_route and hasattr(self.local_route, 'motor_output'):
                        self.local_route.motor_output(out_v if live and cmd_live else 0., out_w if live and cmd_live else 0., packet, live, cmd_live)''')
    # Compile temporary candidate files before modifying running sources.
    for name,source in [('mapping-candidate.py',new_mapping),('bridge-candidate.py',new_bridge)]:
        p=backup/name;p.write_text(source);py_compile.compile(str(p),doraise=True)
    try:
        mapping.write_text(new_mapping);bridge.write_text(new_bridge)
        subprocess.run(['systemctl','--user','restart','hongik-scenarios.service'],check=True,timeout=50)
        time.sleep(4)
        subprocess.run(['systemctl','--user','is-active','--quiet','hongik-scenarios.service'],check=True)
    except Exception:
        shutil.copy2(backup/'mapping_bridge.py',mapping);shutil.copy2(backup/'serial_bridge.py',bridge)
        subprocess.run(['systemctl','--user','restart','hongik-scenarios.service'],timeout=50)
        raise
    (backup/'rollback.py').write_text('from pathlib import Path\nimport shutil,subprocess\np=Path(__file__).parent\nshutil.copy2(p/"mapping_bridge.py",'+repr(str(mapping))+')\nshutil.copy2(p/"serial_bridge.py",'+repr(str(bridge))+')\nsubprocess.run(["systemctl","--user","restart","hongik-scenarios.service"],check=True)\n')
    print('EXPERIMENT_OWNER_INSTALLED; NO ARM/START SENT; BACKUP='+str(backup))

def runtime_diagnostics(home,app):
    result={'installed':True,'processes':[],'agents':[]}
    from serial.tools import list_ports
    result['serial_ports']=[{'device':p.device,'description':p.description,'vid':p.vid,'pid':p.pid,'serial_number':p.serial_number} for p in list_ports.comports()]
    env=os.environ.copy()
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            args=(p/'cmdline').read_bytes().split(b'\0')
            if b'lab.agent' in args:
                result['agents'].append({'pid':p.name,'cwd':str((p/'cwd').resolve()),'command':[x.decode(errors='replace') for x in args if x]})
            if str(app/'jetson/mapping_bridge.py').encode() in args:
                values=dict(x.split(b'=',1) for x in (p/'environ').read_bytes().split(b'\0') if b'=' in x)
                keys=['ROS_DOMAIN_ID','RMW_IMPLEMENTATION','ROS_LOCALHOST_ONLY']
                selected={k:values[k.encode()].decode() for k in keys if k.encode() in values}
                process={'pid':p.name,'ros_environment':selected}
                for fd in ['1','2']:
                    try:
                        log=(p/'fd'/fd).resolve()
                        if log.is_file():process['log_'+fd]=log.read_text(errors='replace')[-12000:]
                    except OSError:pass
                env.update(selected);result['processes'].append(process)
        except OSError:pass
    for topic in ['/d420/manual_cmd_vel','/odom','/d420/experiment/status']:
        try:
            r=subprocess.run(['ros2','topic','info',topic,'--verbose','--no-daemon','--spin-time','2'],env=env,capture_output=True,text=True,timeout=12)
            result[topic]=(r.stdout+r.stderr)[-9000:]
        except subprocess.TimeoutExpired:result[topic]='Discovery timed out'
    result['service_log']=subprocess.run(['journalctl','--user','-u','hongik-scenarios.service','-n','35','--no-pager'],capture_output=True,text=True).stdout[-9000:]
    root=home/'hongik_d420_experiment_lab'
    result['module_source']=subprocess.run(['/usr/bin/python3','-c','import lab,lab.ros_collector as c; print(lab.__file__); print(c.__file__); print(c.RosCollector.__init__.__code__.co_consts)'],cwd=root,capture_output=True,text=True).stdout[-3500:]
    return result


def connection_diagnostics(home,app):
    result={}
    log=home/'hongik_experiment_data/agent.log'
    if log.exists():result['agent_log']=log.read_text(errors='replace')[-22000:]
    result['listeners']=subprocess.run(['ss','-ltnp'],capture_output=True,text=True).stdout[-8000:]
    result['logs']={}
    for name in ['hongik-team-ui.service','hongik-scenarios.service']:
        unit=subprocess.run(['systemctl','--user','cat',name],capture_output=True,text=True).stdout
        for line in unit.splitlines():
            if line.startswith(('StandardOutput=append:','StandardError=append:')):
                path=Path(line.split('append:',1)[1])
                if path.is_file() and path.resolve().is_relative_to(home):result['logs'][str(path)]=path.read_text(errors='replace')[-12000:]
    return result


def start_missing_recorder(root,home):
    """Restore the isolated recording API after reboot, without motor commands."""
    import shlex
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            if b'lab.agent' in (p/'cmdline').read_bytes().split(b'\0') and (p/'cwd').resolve()==root.resolve():
                raise RuntimeError('Recording process exists without a responding API; inspect before replacing')
        except OSError:pass
    private=home/'.config/hongik_d420_lab'
    script=private/'start-recorder.sh'
    script.write_text('#!/bin/bash\nset -e\nsource /opt/ros/humble/setup.bash\nexec /usr/bin/python3 -m lab.agent --root '+shlex.quote(str(home/'hongik_experiment_data'))+'\n')
    script.chmod(0o700)
    units=home/'.config/systemd/user';units.mkdir(parents=True,exist_ok=True)
    name='hongik-experiment-recorder.service';unit=units/name
    marker='# HONGIK D420 isolated experiment recorder\n'
    if unit.exists() and not unit.read_text().startswith(marker):raise RuntimeError('Unknown recorder service exists; refused overwrite')
    unit.write_text(marker+'[Unit]\nDescription=HONGIK D420 experiment raw recorder\nAfter=network.target\n\n[Service]\nType=simple\nWorkingDirectory='+str(root)+'\nEnvironmentFile='+str(private/'agent.env')+'\nEnvironment=ROS_DOMAIN_ID=120\nExecStart=/bin/bash '+str(script)+'\nRestart=on-failure\nRestartSec=3\nStandardOutput=append:'+str(home/'hongik_experiment_data/agent.log')+'\nStandardError=append:'+str(home/'hongik_experiment_data/agent.log')+'\n\n[Install]\nWantedBy=default.target\n')
    subprocess.run(['systemctl','--user','daemon-reload'],check=True,timeout=10)
    subprocess.run(['systemctl','--user','enable','--now',name],check=True,timeout=15)
    token=(private/'agent.env').read_text().split('=',1)[1].strip()
    req=urllib.request.Request('http://127.0.0.1:18866/status',headers={'Authorization':'Bearer '+token})
    for _ in range(50):
        try:
            with urllib.request.urlopen(req,timeout=1) as r:
                if r.status==200:return
        except OSError:time.sleep(.2)
    raise RuntimeError('Recorder startup failed; inspect agent.log')


def refresh_recorder(root,home):
    import hashlib
    private=home/'.config/hongik_d420_lab';marker=private/'collector_version'
    version=hashlib.sha256((root/'lab/ros_collector.py').read_bytes()+(root/'lab/agent.py').read_bytes()).hexdigest()
    token=(private/'agent.env').read_text().split('=',1)[1].strip()
    req=urllib.request.Request('http://127.0.0.1:18866/status',headers={'Authorization':'Bearer '+token})
    with urllib.request.urlopen(req,timeout=3) as r:status=json.load(r)
    if status.get('recording'):raise RuntimeError('Recorder active; refused update')
    if marker.exists() and marker.read_text()==version and status.get('ros_running') and status.get('recorder_node','').startswith('hongik_experiment_agent_'):return
    found=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            if b'lab.agent' in (p/'cmdline').read_bytes().split(b'\0') and (p/'cwd').resolve()==root.resolve():found.append(int(p.name))
        except OSError:pass
    if len(found)!=1:raise RuntimeError('Cannot uniquely identify isolated recording agent')
    unit='hongik-experiment-recorder.service'
    owner=subprocess.run(['systemctl','--user','show',unit,'--property=MainPID','--value'],capture_output=True,text=True,timeout=5)
    if owner.stdout.strip()==str(found[0]):
        # Let systemd own replacement so Restart=on-failure cannot create a
        # competing recorder while a manual replacement is being spawned.
        subprocess.run(['systemctl','--user','restart',unit],check=True,timeout=20)
        for _ in range(50):
            try:
                with urllib.request.urlopen(req,timeout=1) as r:data=json.load(r)
                new_pid=subprocess.check_output(['systemctl','--user','show',unit,'--property=MainPID','--value'],text=True,timeout=3).strip()
                if new_pid.isdigit() and data.get('recorder_pid')==int(new_pid) and data.get('ros_running'):
                    marker.write_text(version);return
            except (OSError,subprocess.SubprocessError):pass
            time.sleep(.2)
        raise RuntimeError('Managed recorder restart failed; inspect agent.log')
    os.kill(found[0],signal.SIGTERM)
    for _ in range(40):
        if not Path('/proc',str(found[0])).exists():break
        time.sleep(.1)
    old=Path('/proc',str(found[0]))
    if old.exists():
        # rclpy handles SIGTERM by stopping ROS but Flask can keep listening.
        # Only force-close the positively identified idle isolated sidecar.
        if b'lab.agent' not in (old/'cmdline').read_bytes().split(b'\0') or (old/'cwd').resolve()!=root.resolve():raise RuntimeError('Recorder identity changed; refused termination')
        os.kill(found[0],signal.SIGKILL);time.sleep(.5)
    env=os.environ.copy();env.update(D420_AGENT_TOKEN=token,ROS_DOMAIN_ID='120')
    with (home/'hongik_experiment_data/agent.log').open('ab') as log:
        process=subprocess.Popen(['/usr/bin/python3','-m','lab.agent','--root',str(home/'hongik_experiment_data')],cwd=root,env=env,stdout=log,stderr=log,stdin=subprocess.DEVNULL,start_new_session=True)
    (private/'agent.pid').write_text(str(process.pid)+'\n')
    for _ in range(50):
        try:
            with urllib.request.urlopen(req,timeout=1) as r:
                data=json.load(r)
                if r.status==200 and data.get('recorder_pid')==process.pid:marker.write_text(version);return
        except Exception:time.sleep(.2)
    raise RuntimeError('Updated recorder health check failed; see isolated agent.log')


def refresh_owner(root,home):
    import hashlib
    version=hashlib.sha256((root/'lab/core.py').read_bytes()+(root/'lab/jetson_adapter.py').read_bytes()).hexdigest()
    marker=home/'.config/hongik_d420_lab/owner_version'
    if marker.exists() and marker.read_text()==version:return
    for name in ['core.py','jetson_adapter.py']:py_compile.compile(str(root/'lab'/name),doraise=True)
    subprocess.run(['systemctl','--user','restart','hongik-scenarios.service'],check=True,timeout=50)
    time.sleep(5)
    subprocess.run(['systemctl','--user','is-active','--quiet','hongik-scenarios.service'],check=True)
    marker.write_text(version)


def install_forwarding_key(root,home):
    import ast,re
    source=root/'lab/forward_key.py'
    if not source.exists():return
    public=ast.literal_eval(source.read_text().split('PUBLIC_KEY = ',1)[1].strip())
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+ hongik-d420-forward-only',public):raise RuntimeError('Unexpected dedicated forwarding public key')
    folder=home/'.ssh';folder.mkdir(mode=0o700,exist_ok=True)
    target=folder/'authorized_keys'
    if target.is_symlink():raise RuntimeError('Refused symbolic authorized_keys target')
    text=target.read_text() if target.exists() else ''
    gateway=root/'scripts/maintenance_gateway.py'
    if not gateway.is_file():raise RuntimeError('Restricted maintenance gateway missing')
    command='/usr/bin/python3 '+str(gateway)
    private=home/'.config/hongik_d420_lab'
    if target.exists():
        backup=private/('authorized_keys_before_forwarding_'+time.strftime('%Y%m%d_%H%M%S'))
        shutil.copy2(target,backup);backup.chmod(0o600)
    line='command="'+command+'",restrict,port-forwarding,permitopen="127.0.0.1:18866",permitopen="127.0.0.1:8765" '+public
    if line in text:return
    kept=[entry for entry in text.splitlines() if public.split()[1] not in entry]
    target.write_text('\n'.join(kept+[line])+'\n')
    target.chmod(0o600)


if __name__=='__main__':main()
