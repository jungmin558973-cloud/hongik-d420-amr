"""Forced SSH command: update only this isolated lab; never sends drive goals."""
import json,os,subprocess,sys,py_compile,shutil,time,urllib.request
from pathlib import Path


def main():
    if os.environ.get('SSH_ORIGINAL_COMMAND')!='hongik-update':raise ValueError('Only the lab update command is permitted')
    root=Path(__file__).resolve().parents[1];home=Path.home()
    token=(home/'.config/hongik_d420_lab/agent.env').read_text().split('=',1)[1].strip()
    req=urllib.request.Request('http://127.0.0.1:18866/status',headers={'Authorization':'Bearer '+token})
    with urllib.request.urlopen(req,timeout=3) as r:state=json.load(r)
    if state.get('recording') or state.get('state') in ('MOVING','BRAKING','RETURNING','RECORDING','SETTLING','STOPPING','POSTROLL') or (state.get('v') is not None and abs(state['v'])>.008):raise ValueError('Stop the experiment/recording before maintenance')
    raw=sys.stdin.buffer.read(3*1024*1024+1)
    if len(raw)>3*1024*1024:raise ValueError('Update payload too large')
    files=json.loads(raw)['files'];candidates={}
    for name,source in files.items():
        relative=Path(name)
        allowed=(len(relative.parts)==2 and relative.parts[0]=='lab' and relative.suffix=='.py') or name=='scripts/install_control_remote.py'
        target=root/relative
        if not allowed or not target.resolve().is_relative_to(root) or not isinstance(source,str):raise ValueError('Unexpected update target')
        compile(source,name,'exec');candidates[target]=source
    backup=home/'.config/hongik_d420_lab'/('code_before_update_'+time.strftime('%Y%m%d_%H%M%S'));backup.mkdir()
    for target,source in candidates.items():
        if target.is_file():
            dst=backup/target.relative_to(root);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,dst)
        target.write_text(source)
    result=subprocess.run(['/bin/bash','-lc','source /opt/ros/humble/setup.bash; exec /usr/bin/python3 scripts/install_control_remote.py'],cwd=root,capture_output=True,text=True,timeout=110)
    print(json.dumps({'ok':result.returncode==0,'result':result.stdout[-30000:],'error':result.stderr[-3000:]}))


if __name__=='__main__':
    try:main()
    except Exception as e:print(json.dumps({'ok':False,'error':str(e)}));raise SystemExit(1)
