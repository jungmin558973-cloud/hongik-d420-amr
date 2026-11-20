"""Build verified source/data deliveries without credentials or live DB indexes."""
import hashlib
import json
from pathlib import Path
import re
import zipfile
from datetime import datetime

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT.parent/'deliveries'/'20261121'
DENIED={'__pycache__','.pytest_cache','.venv','runs','.ssh','.git','node_modules'}
PRIVATE={'.env','oauth_client.json','token.json','validation.local.json','forward_key.py'}
VALIDATION=['record-priority-tests.xml','release-review-20261121.json','curve-and-playback-ui.json','analysis-lab-test-results.json']


def digest(data):return hashlib.sha256(data).hexdigest()


def source_files():
    result=[]
    for folder in ['lab','web','scripts','tests','legacy_app']:
        for p in (ROOT/folder).rglob('*'):
            rel=p.relative_to(ROOT)
            if not p.is_file() or set(rel.parts)&DENIED or p.name in PRIVATE:continue
            if p.suffix.lower() in ('.pyc','.log','.pem','.key','.zip','.bin','.elf','.map'):continue
            result.append(p)
    result.extend(p for p in ROOT.iterdir() if p.is_file() and (p.suffix in ('.md','.txt','.cmd','.toml','.py') or p.name in ('.gitignore','.gitattributes','.env.example','connection.json','validation.example.json')))
    result.extend(ROOT/'validation'/name for name in VALIDATION if (ROOT/'validation'/name).is_file())
    return sorted(set(result))


def verify_secret_free(data,path):
    patterns=[rb'-----BEGIN (?:OPENSSH|RSA|EC|DSA) PRIVATE KEY-----',rb'gh[pousr]_[A-Za-z0-9]{30,}',rb'github_pat_[A-Za-z0-9_]{40,}']
    if any(re.search(p,data) for p in patterns):raise ValueError('Credential pattern detected in '+str(path))


def make_zip(target,files,prefix):
    manifest={}
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=4) as z:
        for p in files:
            rel=p.relative_to(ROOT).as_posix();raw=p.read_bytes()
            if prefix:verify_secret_free(raw,rel)
            name=prefix+rel;z.writestr(name,raw)
            manifest[name]={'bytes':len(raw),'sha256':digest(raw)}
        z.writestr(prefix+'DELIVERY_MANIFEST.json',json.dumps({'created':datetime.now().isoformat(),'files':manifest},ensure_ascii=False,indent=2))
    with zipfile.ZipFile(target) as z:
        if z.testzip() is not None:raise ValueError('ZIP CRC verification failed')
        for name,entry in manifest.items():
            if digest(z.read(name))!=entry['sha256']:raise ValueError('ZIP checksum mismatch: '+name)
    return {'file':target.name,'bytes':target.stat().st_size,'files':len(files),'sha256':digest(target.read_bytes())}


def main():
    OUT.mkdir(parents=True,exist_ok=True);source=source_files()
    program=make_zip(OUT/'HONGIK_D420_실험_분석_프로그램_20261121.zip',source,'D420_EXPERIMENT_LAB/')
    print('program',json.dumps(program,ensure_ascii=True),flush=True)
    data=[p for p in (ROOT/'experiments').rglob('*') if p.is_file() and '_camera' not in p.parts and not p.name.startswith('experiment_index.sqlite3') and not p.name.endswith('.tmp') and (p.suffix!='.zip' or p.name=='jetson_raw.zip')]
    data_zip=make_zip(OUT/'HONGIK_D420_실험데이터_분석결과_20261121.zip',data,'')
    print('data',json.dumps(data_zip,ensure_ascii=True),flush=True)
    result={'program':program,'data':data_zip,'excluded':['credentials','runtime SQLite index','duplicate per-condition ZIPs','camera preview cache']}
    (OUT/'배포목록_SHA256.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    publish=[]
    for p in source:
        raw=p.read_bytes()
        try:raw.decode('utf-8')
        except UnicodeError:continue # CAD/images stay in full program ZIP.
        publish.append({'local':p.relative_to(ROOT).as_posix(),'path':'experiment_lab/'+p.relative_to(ROOT).as_posix(),
                        'sha':hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),'bytes':len(raw)})
    (OUT/'publish-files.json').write_text(json.dumps(publish,ensure_ascii=False),encoding='utf-8')
    print('publish_files',len(publish),flush=True)


if __name__=='__main__':main()
