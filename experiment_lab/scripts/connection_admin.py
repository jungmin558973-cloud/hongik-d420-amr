"""Authenticated loopback setup maintenance; fixed operations, no drive API."""
import hmac
import json
import shlex
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import keyring


INSPECT="""python3 - <<'PY'
from pathlib import Path
import json,subprocess,os
h=Path.home()
out={'home':str(h),'files':{},'services':{}}
for rel in ['d420_ws/scenario_lab/jetson/mapping_bridge.py','d420_ros_integrated/serial_bridge.py']:
 p=h/rel;out['files'][rel]={'exists':p.exists(),'resolved':str(p.resolve())}
 if p.exists() and 'mapping_bridge' in rel:out['files'][rel]['source']=p.read_text()[:7000]
for unit in ['hongik-scenarios.service','hongik-team-ui.service','hongik-depth-observer.service']:
 r=subprocess.run(['systemctl','--user','show',unit,'--property=ActiveState,MainPID,ExecStart,FragmentPath'],capture_output=True,text=True)
 out['services'][unit]=r.stdout+r.stderr
out['bridge_candidates']=[str(p) for p in h.glob('**/serial_bridge.py') if '.cache' not in p.parts][:30]
r=subprocess.run(['ps','-eo','pid,args'],capture_output=True,text=True)
out['processes']=[l for l in r.stdout.splitlines() if any(x in l for x in ['mapping_bridge','serial_bridge','scenario_lab','lab.agent']) and not 'python3 - <<' in l]
print(json.dumps(out))
PY"""


def start(connector):
    token=keyring.get_password('HONGIK_CAFE_D420','setup_token')
    if not token:
        import secrets
        token=secrets.token_urlsafe(40);keyring.set_password('HONGIK_CAFE_D420','setup_token',token)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):self.handle_request(False)
        def do_POST(self):self.handle_request(True)
        def handle_request(self,write):
            if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):
                self.send_error(403);return
            try:
                if self.path=='/inspect' and not write:
                    connector.ensure_ssh();data=json.loads(connector.run(INSPECT,timeout=50))
                elif self.path=='/refresh' and write:
                    connector.ensure_ssh()
                    root=Path(__file__).resolve().parents[1]
                    home=connector.run('printf "%s" "$HOME"');remote=home+'/hongik_d420_experiment_lab'
                    sftp=connector.ssh.open_sftp()
                    try:
                        for p in (root/'lab').glob('*.py'):sftp.put(str(p),remote+'/lab/'+p.name)
                        sftp.put(str(root/'scripts/install_control_remote.py'),remote+'/scripts/install_control_remote.py');sftp.put(str(root/'scripts/maintenance_gateway.py'),remote+'/scripts/maintenance_gateway.py')
                    finally:sftp.close()
                    result=connector.run('source /opt/ros/humble/setup.bash; cd '+shlex.quote(remote)+'; /usr/bin/python3 scripts/install_control_remote.py',timeout=100)
                    data={'ok':True,'result':result}
                else:self.send_error(404);return
                payload=json.dumps(data).encode();self.send_response(200)
            except Exception as e:
                payload=json.dumps({'error':str(e)}).encode();self.send_response(409)
            self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(payload)
    server=ThreadingHTTPServer(('127.0.0.1',18867),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start();return server
