"""Local-first artifacts, crash recovery, checksums and durable metadata."""
import csv
import hashlib
import html
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
import zipfile
import re
from datetime import datetime,timezone,timedelta
from contextlib import contextmanager


def dump(path,data):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp')
    with tmp.open('w',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)


def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def read_jsonl(path):
    rows=[]
    if not Path(path).exists():return rows
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        try:rows.append(json.loads(line))
        except ValueError:pass # Preserve damaged source; recover complete records only.
    return rows


def csv_write(path,rows):
    with Path(path).open('w',newline='',encoding='utf-8-sig') as f:
        if rows:
            keys=list(dict.fromkeys(k for r in rows for k in r))
            w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)
        f.flush();os.fsync(f.fileno())


class Store:
    def __init__(self,root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.db=self.root/'experiment_index.sqlite3';self.lock=threading.RLock()
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS experiment_index(
            run_id TEXT PRIMARY KEY,path TEXT NOT NULL,status TEXT,simulated INTEGER,config TEXT,metrics TEXT,
            upload_status TEXT DEFAULT 'PENDING',created REAL);
            CREATE TABLE IF NOT EXISTS upload_queue(run_id TEXT PRIMARY KEY,attempts INTEGER DEFAULT 0,
            next_at REAL DEFAULT 0,state TEXT DEFAULT 'PENDING',error TEXT,progress REAL DEFAULT 0,remote_id TEXT);
            CREATE TABLE IF NOT EXISTS trial_queue(key TEXT PRIMARY KEY,data TEXT,status TEXT,run_id TEXT);''')
        self.reindexed=self.rebuild_index()

    def rebuild_index(self):
        """Recover file-backed runs when an index was removed or recreated."""
        restored=[]
        with self.connect() as db:
            known={r[0] for r in db.execute('SELECT run_id FROM experiment_index')}
            for manifest in self.root.rglob('manifest.json'):
                try:
                    path=manifest.parent
                    if not path.resolve().is_relative_to(self.root):continue
                    data=json.loads(manifest.read_text(encoding='utf-8'));rid=data.get('run_id','')
                    if rid in known or not re.fullmatch(r'RUN_[A-Za-z0-9_-]{10,80}',rid):continue
                    config=json.loads((path/'config.json').read_text(encoding='utf-8'))
                    metrics=json.loads((path/'metrics.json').read_text(encoding='utf-8')) if (path/'metrics.json').exists() else {}
                    status=data.get('status','ABORTED')
                    db.execute('INSERT INTO experiment_index(run_id,path,status,simulated,config,metrics,created) VALUES(?,?,?,?,?,?,?)',(rid,str(path),status,bool(data.get('simulated')),json.dumps(config),json.dumps(metrics),manifest.stat().st_mtime))
                    if status not in ('RECORDING','SAVING'):db.execute('INSERT OR IGNORE INTO upload_queue(run_id) VALUES(?)',(rid,))
                    restored.append(rid);known.add(rid)
                except (OSError,ValueError,KeyError):continue
        return restored

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.db,timeout=10);db.row_factory=sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
        try:
            with db:yield db
        finally:db.close()

    def begin(self,c,simulated=False):
        now=time.time();seoul=datetime.fromtimestamp(now,timezone(timedelta(hours=9)))
        modes={(False,'OFF'):'A',(True,'OFF'):'B',(False,'LEVEL'):'C',(True,'LEVEL'):'D',(False,'PREDICTIVE'):'E',(True,'PREDICTIVE'):'F'}
        mode=modes[(c.get('s_curve',True),c.get('tray','OFF'))]
        condition=f"{mode}_{c.get('distance',1.6):g}m_{c.get('speed',.2)*100:g}cmps"
        strength=c.get('s_curve_strength',100) if c.get('s_curve',True) else 0
        if c.get('s_curve',True) and strength!=100:condition+=f'_S{strength:g}'
        if c.get('scenario')=='record_only':condition='기록전용_'+condition
        if simulated:condition='MOCK_'+condition
        # Parameter changes get their own group; notes and repeat count do not.
        identity={k:c.get(k) for k in ('distance','speed','s_curve','tray','scenario')}
        identity['s_curve_strength']=strength
        group=self.root/seoul.strftime('%Y-%m-%d')/condition
        stem_prefix=(f"L{c.get('distance',1.6):.2f}m_V{c.get('speed',.2)*100:g}cmps_"
              f"S{'ON' if c.get('s_curve',True) else 'OFF'}_T{c.get('tray','OFF')}_SC{strength:g}_"
              f"a{c.get('acceleration',.2):g}_d{c.get('deceleration',.25):g}_j{c.get('jerk',.5):g}_"
              f"Obs{c.get('settle',10)+c.get('post_roll',0):g}s_")
        with self.lock:
            group.mkdir(parents=True,exist_ok=True)
            dump(group/'condition.json',identity)
            repeat=sum(child.is_dir() for child in group.iterdir())+1
            for attempt in range(100):
                uid=uuid.uuid4().hex;stamp=seoul.strftime('%H%M%S_%f')[:-3]
                rid='RUN_'+seoul.strftime('%Y%m%d_')+stamp+'_'+uid
                stem=stem_prefix+stamp+'_'+uid[:10]+(f'_n{attempt+1}' if attempt else '')
                if any(group.glob(stem+'_*')):continue
                with self.connect() as db:
                    if db.execute('SELECT 1 FROM experiment_index WHERE run_id=?',(rid,)).fetchone():continue
                path=group/(stem+'_촬영중')
                try:path.mkdir() # Exclusive reservation; never reuse/overwrite a folder.
                except FileExistsError:continue
                break
            else:raise ValueError('고유 실험 이름 생성 실패: 기존 데이터는 변경하지 않았습니다.')
        label={'run_id':rid,'condition':condition,'repeat':repeat,'folder_stem':stem,'display_name':stem+'_촬영중','folder':str(path)}
        dump(path/'run_label.json',label)
        dump(path/'config.json',c);dump(path/'manifest.json',{'run_id':rid,'status':'RECORDING','simulated':simulated,'display_name':label['display_name'],'repeat':repeat,'files':{}})
        from .run_notes import write_notes
        write_notes(path,c,label)
        with self.connect() as db:db.execute('INSERT INTO experiment_index(run_id,path,status,simulated,config,created) VALUES(?,?,?,?,?,?)',(rid,str(path),'RECORDING',simulated,json.dumps(c),now))
        return rid,path

    def list(self):
        with self.connect() as db:rows=[dict(r) for r in db.execute('SELECT * FROM experiment_index ORDER BY created DESC')]
        for r in rows:
            r['config']=json.loads(r['config']);r['metrics']=json.loads(r['metrics'] or '{}')
            label=Path(r['path'])/'run_label.json'
            if label.exists():r['label']=json.loads(label.read_text(encoding='utf-8'))
        return rows

    def path(self,rid):
        with self.connect() as db:r=db.execute('SELECT path FROM experiment_index WHERE run_id=?',(rid,)).fetchone()
        if not r:raise ValueError('Unknown run_id')
        return Path(r['path'])

    def bundle_path(self,rid):
        path=self.path(rid)
        return path.parent/(path.parent.name+'_'+path.name+'.zip')

    def organize(self,rid,path,outcome):
        label_path=path/'run_label.json'
        if not label_path.exists():return path
        label=json.loads(label_path.read_text(encoding='utf-8'))
        stem=label.get('folder_stem',rid)
        name=stem+'_'+outcome['folder_status']
        target=path.with_name(name)
        if not path.resolve().is_relative_to(self.root):raise ValueError('Run path outside data root')
        if target!=path:
            counter=0
            while target.exists():
                counter+=1;target=path.with_name(name+'_'+rid[-8:]+f'_{counter}')
            try:path.rename(target)
            except OSError:outcome['folder_rename_pending']=name;target=path
        label.update(folder=str(target),display_name=target.name,completion=outcome)
        dump(target/'run_label.json',label)
        with self.connect() as db:db.execute('UPDATE experiment_index SET path=? WHERE run_id=?',(str(target),rid))
        return target

    def bundle(self,rid):
        path=self.path(rid);target=self.bundle_path(rid)
        manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
        if manifest['status'] in ('RECORDING','SAVING'):raise ValueError('Wait for recording to finish')
        needed=sum(p.stat().st_size for p in path.rglob('*') if p.is_file())
        import shutil
        if shutil.disk_usage(path).free<needed+512*1024**2:raise ValueError('ZIP space insufficient; original files preserved')
        temp=target.with_suffix('.zip.tmp')
        try:
            with zipfile.ZipFile(temp,'w',zipfile.ZIP_STORED,allowZip64=True) as z:
                for p in sorted(path.rglob('*')):
                    if p.is_file() and not p.name.endswith('.tmp'):z.write(p,Path(path.name)/p.relative_to(path))
            # CRC readback verifies the completed single-file handoff.
            with zipfile.ZipFile(temp) as z:
                if z.testzip() is not None:raise ValueError('ZIP verification failed; originals preserved')
            os.replace(temp,target)
        finally:temp.unlink(missing_ok=True)
        return target

    def finalize(self,rid,status,clock):
        from .sync import merge
        from .analysis import metrics
        path=self.path(rid);frames=read_jsonl(path/'camera_frames.jsonl');robot=read_jsonl(path/'robot_telemetry.jsonl');events=read_jsonl(path/'events.jsonl')
        config=json.loads((path/'config.json').read_text(encoding='utf-8'))
        if (path/'camera_analysis.jsonl').exists():frames=read_jsonl(path/'camera_analysis.jsonl')
        # End measurement before recovery. Raw recovery events remain for safety auditing.
        robot=[r for r in robot if r.get('segment','forward')=='forward']
        csv_write(path/'camera_frames.csv',frames)
        synced=merge(frames,robot,clock);csv_write(path/'synchronized.csv',synced)
        # Convert Jetson event UTC to camera UTC for metric timing; originals untouched.
        off=clock.get('jetson_minus_windows_s',0.)
        aligned=[{**r,'utc':r['utc']-off} if r.get('source')=='jetson' and 'utc' in r else r for r in events]
        aligned_robot=[{**r,'utc':r['utc']-off} for r in robot]
        m=metrics(frames,aligned_robot,aligned,config);m['clock']=clock
        from .completion import assess
        m['completion']=assess(status,config,frames,aligned)
        m['analysis_status']='COMPLETE' if (path/'camera_analysis.jsonl').exists() or not config.get('record_priority') else 'PENDING_OFFLINE_ANALYSIS'
        m['video_file']='webcam_raw.avi' if (path/'webcam_raw.avi').exists() else 'webcam_raw.mp4'
        from .protocol import validate_run
        m['protocol']=validate_run(path,aligned,config,frames)
        from .peak_event import export_peak
        m['peak_event']=export_peak(path,frames,aligned_robot,aligned,clock)
        if clock.get('quality')!='good':m['zero_to_stop_s']=None
        dump(path/'metrics.json',m)
        self.plots(path,synced)
        path=self.organize(rid,path,m['completion'])
        dump(path/'completion.json',m['completion'])
        dump(path/'metrics.json',m)
        from .run_notes import write_notes
        label=json.loads((path/'run_label.json').read_text(encoding='utf-8')) if (path/'run_label.json').exists() else {'run_id':rid,'display_name':rid}
        write_notes(path,config,label,m)
        body=html.escape(json.dumps({'status':status,'config':config,'metrics':m},ensure_ascii=False,indent=2))
        (path/'report.html').write_text('<!doctype html><meta charset="utf-8"><title>'+rid+'</title><h1>HONGIK CAFE — '+rid+'</h1><p>Forward measurement only. Reverse is recovery. Simulated data are labelled in manifest. Pixel units until calibrated.</p><a href="' + m['video_file'] + '">Video</a><pre>'+body+'</pre>'+''.join(f'<img width="700" src="plots/{x}.png">' for x in ('slosh','velocity','brake','tray')),encoding='utf-8')
        old=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
        files={}
        for p in path.rglob('*'):
            if p.is_file() and p.name!='manifest.json':
                with p.open('r+b') as f:os.fsync(f.fileno())
                files[p.relative_to(path).as_posix()]={'sha256':sha(p),'size':p.stat().st_size}
        dump(path/'manifest.json',{**old,'status':status,'clock':clock,'files':files,'closed_utc':time.time()})
        with self.connect() as db:
            db.execute('UPDATE experiment_index SET status=?,metrics=? WHERE run_id=?',(status,json.dumps(m),rid))
            db.execute('INSERT OR IGNORE INTO upload_queue(run_id) VALUES(?)',(rid,))
        try:
            archive=self.bundle(rid);m['bundle']={'ready':True,'file':archive.name,'bytes':archive.stat().st_size}
        except Exception as e:m['bundle']={'ready':False,'error':str(e),'original_files_preserved':True}
        # The bundle contains the closed manifest and preceding metrics. Bundle
        # availability belongs to the index to avoid recursively changing hashes.
        with self.connect() as db:db.execute('UPDATE experiment_index SET metrics=? WHERE run_id=?',(json.dumps(m),rid))
        return m

    def plots(self,path,rows):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        (path/'plots').mkdir(exist_ok=True)
        specs={'slosh':('displacement_px',),'velocity':('v','requested_v','final_v'),
               'brake':('v',),'tray':('roll_deg','pitch_deg')}
        for name,keys in specs.items():
            fig,ax=plt.subplots(figsize=(9,3))
            for key in keys:
                good=[r for r in rows if r.get(key) is not None]
                if good:ax.plot([r['t'] for r in good],[r[key] for r in good],label=key)
            ax.set(xlabel='Windows timeline (s)',ylabel='px' if name=='slosh' else 'deg' if name=='tray' else 'm/s',title=name)
            if ax.lines:ax.legend()
            else:ax.text(.5,.5,'N/A — no valid data',ha='center',transform=ax.transAxes)
            fig.tight_layout();fig.savefig(path/'plots'/f'{name}.png');plt.close(fig)

    def recover(self):
        recovered=[]
        for r in self.list():
            if r['status'] in ('RECORDING','SAVING'):
                path=Path(r['path'])
                with (path/'events.jsonl').open('a',encoding='utf-8') as f:
                    f.write(json.dumps({'event':'ABORT','reason':'Process restart recovery','utc':time.time()})+'\n')
                self.finalize(r['run_id'],'ABORTED',{'quality':'unknown','uncertainty_s':1.,'jetson_minus_windows_s':0.})
                recovered.append(r['run_id'])
        return recovered


class Journal:
    def __init__(self,path):self.path=Path(path);self.lock=threading.Lock()
    def append(self,name,row):
        with self.lock,(self.path/name).open('a',encoding='utf-8') as f:
            f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n');f.flush();os.fsync(f.fileno())
