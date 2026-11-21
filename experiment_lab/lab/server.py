"""Loopback-only Windows experiment API with CSRF and strict state gates."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlparse
from flask import Flask,request,jsonify,send_file,send_from_directory,Response
from .backend import MockBackend,RemoteBackend,CameraOnlyBackend
from .runtime import Manager
from .core import Config,matrix
from .analysis import compare,recommend
from .storage import dump


ROOT=Path(__file__).resolve().parents[1]


def create_app(manager=None,real=False,camera_only=False):
    if manager is None:
        connection_file=Path(os.environ.get('D420_CONNECTION_FILE',str(ROOT/'connection.json')))
        startup_connection=json.loads(connection_file.read_text(encoding='utf-8')) if connection_file.exists() else {}
        agent_token=os.environ.get('D420_AGENT_TOKEN','')
        if real and not agent_token:
            import keyring
            agent_token=keyring.get_password('HONGIK_CAFE_D420','agent_token') or ''
        backend=CameraOnlyBackend() if camera_only else RemoteBackend(os.environ.get('D420_AGENT_URL',startup_connection.get('agent_url','http://127.0.0.1:18866')),agent_token) if real else MockBackend()
        manager=Manager(os.environ.get('D420_DATA_ROOT',str(ROOT/'experiments')),backend)
    m=manager;app=Flask(__name__,static_folder=str(ROOT/'web'),static_url_path='/assets');app.config['MAX_CONTENT_LENGTH']=65536
    app.extensions['manager']=m;token=secrets.token_urlsafe(32)
    connection_path=Path(os.environ.get('D420_CONNECTION_FILE',str(ROOT/'connection.json')))
    connection=json.loads(connection_path.read_text(encoding='utf-8')) if connection_path.exists() else {}
    port=connection.get('local_port')
    navigation_url=f'http://127.0.0.1:{int(port)}/navigation' if type(port) is int else None
    from .teleop import TeleopProxy
    teleop=TeleopProxy(f'http://127.0.0.1:{int(port)}',m) if type(port) is int and not m.backend.simulated else None
    app.extensions['teleop']=teleop
    analysis_jobs={}
    from .analysis_view import register_analysis_view
    register_analysis_view(app,m,ROOT)
    @app.post('/api/analyze/<rid>')
    def offline_analysis(rid):
        browser_only=(request.get_json(silent=True) or {}).get('browser_only') is True
        with m.lock:
            if m.active or m.saving or m.analysis_running:raise ValueError('Finish capture/recovery/analysis first')
            if teleop and teleop.status().get('enabled'):raise ValueError('Stop manual control before offline analysis')
            m.store.path(rid)
            with m.store.connect() as db:
                if db.execute("SELECT 1 FROM upload_queue WHERE state='UPLOADING'").fetchone():raise ValueError('Wait for current upload before offline analysis')
            m.analysis_running=True;analysis_jobs[rid]={'state':'RUNNING'}
        def work():
            try:
                progress=lambda **data:analysis_jobs[rid].update(data)
                if browser_only:
                    from .browser_video import make_browser_video
                    path=m.store.path(rid);make_browser_video(path,progress)
                    manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
                    result=m.store.finalize(rid,manifest['status'],manifest['clock'])
                else:
                    from .offline import analyze
                    result=analyze(m.store,rid,progress=progress)
                analysis_jobs[rid]={'state':'COMPLETE','percent':100,'metrics':result}
                if m.run_id==rid:m.metrics=result
            except Exception as e:analysis_jobs[rid]={'state':'ERROR','error':str(e)}
            finally:m.analysis_running=False
        threading.Thread(target=work,daemon=True).start();return jsonify(ok=True)
    @app.get('/api/analysis/<rid>')
    def analysis_status(rid):return jsonify(analysis_jobs.get(rid,{'state':'NOT_STARTED'}))

    @app.before_request
    def guard():
        host=request.host.split(':')[0]
        if host not in ('127.0.0.1','localhost') or request.remote_addr not in ('127.0.0.1','::1'):return jsonify(error='Local use only'),403
        if request.method=='POST':
            origin=request.headers.get('Origin')
            if origin and origin.rstrip('/')!=request.host_url.rstrip('/'):return jsonify(error='Cross-origin rejected'),403
            if not secrets.compare_digest(request.headers.get('X-Lab-Token',''),token):return jsonify(error='Session token required'),403

    @app.errorhandler(ValueError)
    def value_error(e):return jsonify(ok=False,error=str(e)),409
    @app.errorhandler(Exception)
    def error(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e,HTTPException):return e
        return jsonify(ok=False,error=(m.start_step+' 단계: ' if m.start_step else '')+type(e).__name__+': 연결 응답 오류; 로컬 데이터 보존'),500

    @app.get('/')
    @app.get('/experiment-lab')
    def index():return send_from_directory(ROOT/'web','index.html')
    @app.get('/api/session')
    def session():return jsonify(token=token,navigation_url=navigation_url,real=not m.backend.simulated)
    @app.get('/api/status')
    def status():return jsonify(**m.status(),teleop=teleop.status() if teleop else {'enabled':False,'error':'connection.json missing'})
    @app.post('/api/preflight')
    def preflight():
        reasons=[]
        try:
            c=Config.parse(request.get_json() or {})
            try:m.backend.validation.check(c)
            except ValueError as e:reasons.append('현재 실차 검증 범위: '+str(e))
            if not m.backend.simulated and c.s_curve and c.s_curve_strength not in (0,100) and not m.robot.get('capabilities',{}).get('s_curve_strength'):
                reasons.append('Jetson S-curve 0~100 제어 모듈 업데이트 필요')
            camera=m.camera.poll()
            if not camera.get('ready'):reasons.append('카메라 연결 필요')
            if not m.backend.simulated and c.record_priority and (camera.get('width',0)<1920 or camera.get('height',0)<1080):reasons.append('C920 최고 화질 1920×1080 연결 필요')
            if c.require_4k and (camera.get('width',0)<3840 or camera.get('height',0)<2160):reasons.append('4K 조건 미충족: C920 예비시험을 선택하거나 4K 장치를 연결하세요')
            if not c.record_priority and m.roi.get('method')=='marker_blue' and not camera.get('measurement',{}).get('reference_locked'):reasons.append('초록선 위치의 정지 기준면 저장 필요')
            if not getattr(m.backend,'can_arm',True):
                reasons.insert(0,'Jetson SSH 응답 지연·연결 재시도 중입니다.' if m.robot.get('connection_error') else 'Jetson 제어 상태 수신 대기 중입니다.')
            age=m.robot.get('odom_age')
            if not m.backend.simulated and (age is None or age>.3):reasons.append('최신 오도메트리 미수신: 주행 허용 불가')
            if not m.backend.simulated:
                checks=m.robot.get('checks',{})
                if checks.get('live') is not True:reasons.append('차체 피드백 수신 대기')
                if c.obstacle_stop and checks.get('sensors') is not True:reasons.append('LiDAR/D435 최신 안전 관측 대기')
                if c.obstacle_stop and checks.get('clear_front') is not True:reasons.append('전방 안전 공간 확인 필요')
        except ValueError as e:reasons.append(str(e))
        return jsonify(ready=not reasons,blockers=reasons)
    @app.post('/api/teleop/arm')
    def teleop_arm():
        if not teleop:raise ValueError('connection.json missing')
        return jsonify(teleop.arm(request.get_json() or {}))
    @app.post('/api/teleop/input')
    def teleop_input():
        if not teleop:raise ValueError('Teleop unavailable')
        return jsonify(teleop.input(request.get_json() or {}))
    @app.post('/api/teleop/stop')
    def teleop_stop():
        if teleop:teleop.stop(force=True)
        return jsonify(ok=True)
    @app.post('/api/heartbeat')
    def heartbeat():m.browser_beat();return jsonify(ok=True)
    @app.get('/api/preview.jpg')
    def preview():
        if not m.camera.preview.exists():return jsonify(error='Camera not ready'),404
        # Close file before sending response; Windows otherwise locks atomic replacement.
        return Response(m.camera.preview.read_bytes(),mimetype='image/jpeg')

    @app.post('/api/camera')
    def camera():
        d=request.get_json() or {};source=d.get('source','synthetic')
        if isinstance(source,str) and source.isdigit():source=int(source)
        if source not in ('synthetic','auto') and type(source) is not int:
            p=Path(str(source)).resolve()
            if not p.is_file() or p.suffix.lower() not in ('.mp4','.avi','.mkv','.mov'):raise ValueError('Choose a local video file or camera index')
            source=str(p)
        options={'source':source,'backend':d.get('backend','DSHOW')}
        m.auto_camera_requested=source=='auto'
        if source!='synthetic':
            from .devices import resolve
            options=resolve(source,d.get('backend','DSHOW'))
        m.start_camera({**options,'width':int(d.get('width',1920)),'height':int(d.get('height',1080)),'fps':int(d.get('fps',30))})
        return jsonify(ok=True)
    @app.get('/api/camera/devices')
    def camera_devices():
        from .devices import devices
        return jsonify(devices=devices())

    @app.post('/api/roi')
    def roi():
        if m.active:raise ValueError('ROI settings locked during recording')
        d=request.get_json() or {}
        for k in ('liquid_roi','anchor_roi'):
            from .vision import Tracker
            s=m.camera.poll();Tracker.rect(d.get(k),(s.get('height',0),s.get('width',0)))
        m.roi=d;m.camera.send('roi',settings=d);return jsonify(ok=True)

    @app.post('/api/seek')
    def seek():
        if m.active:raise ValueError('Replay seek locked during recording')
        seconds=float((request.get_json() or {}).get('seconds',0))
        if not 0<=seconds<=86400:raise ValueError('Invalid video time')
        m.camera.send('seek',seconds=seconds);return jsonify(ok=True)
    @app.post('/api/reference/capture')
    def capture_reference():
        if m.active or (teleop and teleop.enabled):raise ValueError('Stop recording/control before capturing stationary reference')
        if (request.get_json() or {}).get('stationary_confirmed') is not True:raise ValueError('Confirm robot stationary and liquid settled at stop location')
        (m.camera.root/'reference.json').unlink(missing_ok=True)
        m.camera.send('reference');return jsonify(ok=True,message='Collecting >=30 valid frames over >=2 seconds; unstable reference rejected')

    @app.post('/api/arm')
    def arm():m.arm(request.get_json() or {});return jsonify(ok=True)
    @app.post('/api/start')
    def start():
        if teleop and teleop.enabled:raise ValueError('Stop manual control before automatic experiment')
        return jsonify(ok=True,run_id=m.start(request.get_json() or {}))
    @app.post('/api/record')
    def record():return jsonify(ok=True,run_id=m.start(request.get_json() or {},True))
    @app.post('/api/record/finish')
    def finish_recording():
        if not m.active or not m.record_only:raise ValueError('Record-only session required')
        m.finish('COMPLETE');return jsonify(ok=True)
    @app.post('/api/stop')
    @app.post('/api/pause')
    def stop():
        if teleop:teleop.stop()
        m.stop('Operator STOP / pause (re-ARM required)');return jsonify(ok=True)
    @app.get('/api/runs')
    def runs():return jsonify(runs=m.store.list())
    @app.get('/api/research-protocol')
    def research_protocol():
        groups={}
        for row in m.store.list():
            c=row['config'];key=json.dumps({k:c.get(k) for k in ('distance','speed','s_curve','s_curve_strength','tray','cup','fill_pct','lid','camera_setting','diameter_mm','water_g')},sort_keys=True)
            group=groups.setdefault(key,{'condition':json.loads(key),'valid_run_ids':[],'invalid_run_ids':[],'required':3})
            valid=row['metrics'].get('protocol',{}).get('valid_for_research_protocol') and not row.get('simulated') and row['status']=='COMPLETE'
            group['valid_run_ids' if valid else 'invalid_run_ids'].append(row['run_id'])
        return jsonify(groups=list(groups.values()),minimum_cruise_s=1.5,minimum_native_resolution=[1920,1080],automatic_stop_required=True)
    @app.post('/api/release')
    def release_control():
        if m.active:raise ValueError('Finish/stop experiment first')
        m.backend.release();m.state='READY';return jsonify(ok=True)
    @app.get('/api/compare')
    def comparison():return jsonify(groups=compare(m.store.list()))
    @app.get('/api/recommend')
    def recommendations():return jsonify(recommend(m.store.list(),m.backend.validation))
    @app.post('/api/recommend/apply')
    def apply_recommendation():
        if m.active:raise ValueError('Finish current run first')
        result=recommend(m.store.list(),m.backend.validation)
        if result.get('status')!='REVIEW_REQUIRED':raise ValueError('No supported recommendation')
        candidate=Config.parse(result['candidate']);m.backend.validation.check(candidate)
        return jsonify(config=asdict(candidate),applied_to='UI draft only; ARM still required')
    @app.post('/api/upload/retry')
    def retry():m.uploads.retry((request.get_json() or {}).get('run_id'));return jsonify(ok=True)

    @app.get('/api/matrix')
    def get_matrix():return jsonify(rows=matrix(validation=m.backend.validation))
    @app.post('/api/queue')
    def make_queue():
        if m.active:raise ValueError('Queue locked during run')
        d=request.get_json() or {};repeats=d.get('repeats',3)
        if type(repeats) is not int or not 1<=repeats<=20:raise ValueError('Repeat 1..20')
        rows=matrix(repeats,d.get('seed'),m.backend.validation)
        with m.store.connect() as db:
            for r in rows:db.execute('INSERT OR IGNORE INTO trial_queue(key,data,status) VALUES(?,?,?)',(r['key'],json.dumps(r),r['status']))
        return jsonify(rows=rows)
    @app.get('/api/queue')
    def get_queue():
        with m.store.connect() as db:rows=[dict(r) for r in db.execute("SELECT * FROM trial_queue WHERE key LIKE 'record-v2-%'")]
        for r in rows:r['data']=json.loads(r['data'])
        return jsonify(rows=rows)
    @app.post('/api/queue/start')
    def start_queue():
        d=request.get_json() or {};key=d.get('key')
        if d.get('confirmed') is not True:raise ValueError('Each trial requires operator confirmation')
        with m.store.connect() as db:r=db.execute('SELECT * FROM trial_queue WHERE key=?',(key,)).fetchone()
        if not r or r['status'] in ('RUNNING','COMPLETE'):raise ValueError('Completed or unknown queue item')
        rid=m.start(json.loads(r['data'])['config'])
        with m.store.connect() as db:db.execute("UPDATE trial_queue SET status='RUNNING',run_id=? WHERE key=?",(rid,key))
        return jsonify(ok=True,run_id=rid)

    @app.post('/api/folder')
    def open_folder():
        if os.name=='nt':os.startfile(str(m.store.root))
        return jsonify(path=str(m.store.root))
    @app.post('/api/folder/<rid>')
    def open_run_folder(rid):
        path=m.store.path(rid)
        if os.name=='nt':os.startfile(str(path))
        return jsonify(path=str(path))
    @app.get('/files/<rid>/<path:name>')
    def files(rid,name):
        path=m.store.path(rid);target=(path/name).resolve()
        if not target.is_relative_to(path.resolve()) or not target.is_file():raise ValueError('Unknown artifact')
        return send_file(target,as_attachment=request.args.get('download')=='1',max_age=0,conditional=True)
    @app.get('/api/bundle/<rid>')
    def download_bundle(rid):
        path=m.store.bundle_path(rid)
        if not path.is_file():raise ValueError('No bundle yet; finish recording or offline analysis first')
        return send_file(path,as_attachment=True,max_age=0,conditional=True)

    @app.after_request
    def headers(r):
        r.headers['Cache-Control']='no-store';r.headers['X-Content-Type-Options']='nosniff'
        r.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
        return r
    return app


def main():
    p=argparse.ArgumentParser();p.add_argument('--real',action='store_true');p.add_argument('--port',type=int,default=18865)
    p.add_argument('--mock-camera',action='store_true');p.add_argument('--camera-only',action='store_true');p.add_argument('--camera-source',type=int);p.add_argument('--auto-camera',action='store_true')
    args=p.parse_args();app=create_app(real=args.real,camera_only=args.camera_only);m=app.extensions['manager']
    if args.mock_camera:m.start_camera({'source':'synthetic'})
    elif args.auto_camera or args.camera_source is not None:
        from .devices import resolve
        m.auto_camera_requested=args.auto_camera
        try:m.start_camera(resolve('auto' if args.auto_camera else args.camera_source,'DSHOW'))
        except ValueError as e:m.camera.status.update(ready=False,error=str(e))
    try:app.run(host='127.0.0.1',port=args.port,threaded=True,use_reloader=False)
    finally:
        if app.extensions.get('teleop'):app.extensions['teleop'].close()
        m.close()

if __name__=='__main__':main()
