"""Use the installed restricted maintenance command without a password window."""
import json,os,subprocess
from connect_real import config,ROOT,STATE


def deploy():
    host,user,known,alias=config();key=STATE/'d420_forward_ed25519'
    files={p.relative_to(ROOT).as_posix():p.read_text(encoding='utf-8') for p in (ROOT/'lab').glob('*.py') if p.name!='forward_key.py'}
    p=ROOT/'scripts/install_control_remote.py';files['scripts/install_control_remote.py']=p.read_text(encoding='utf-8')
    args=[str(__import__('pathlib').Path(os.environ['WINDIR'])/'System32/OpenSSH/ssh.exe'),'-T','-i',str(key),'-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+known,'-o','HostKeyAlias='+alias,user+'@'+host,'hongik-update']
    r=subprocess.run(args,input=json.dumps({'files':files}),capture_output=True,text=True,encoding='utf-8',timeout=125,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if not r.stdout.strip():raise RuntimeError('Maintenance not installed or SSH connection failed')
    result=json.loads(r.stdout)
    if not result.get('ok'):raise RuntimeError(result.get('error','Update refused'))
    return result


if __name__=='__main__':print(json.dumps(deploy(),ensure_ascii=True))
