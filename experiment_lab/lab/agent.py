"""Jetson localhost sidecar. Uses ROS only; no serial access and no auto motion."""
import argparse
import hmac
import json
import os
from pathlib import Path
import re
import threading
import time
import zipfile
from flask import Flask,request,jsonify,send_file
from .ros_collector import RosCollector


def create_agent(root,token,collector=None):
    if not token or len(token)<32:raise ValueError('D420_AGENT_TOKEN must contain >=32 random characters')
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    ros=collector or RosCollector(root);app=Flask(__name__);current={'run':None,'recording':False,'error':''}
    lock=threading.RLock()

    @app.before_request
    def guard():
        if request.remote_addr not in ('127.0.0.1','::1'):return jsonify(error='Use SSH tunnel'),403
        if not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+token):return jsonify(error='Unauthorized'),403

    @app.errorhandler(Exception)
    def error(e):return jsonify(ok=False,error=str(e) if isinstance(e,ValueError) else type(e).__name__),400

    @app.get('/clock')
    def clock():
        received=time.time();return jsonify(receive_utc=received,send_utc=time.time(),mono=time.monotonic())

    @app.get('/status')
    def status():
        return jsonify(**ros.status,recording_run_id=current['run'],recorder_pid=os.getpid(),ros_running=ros.rclpy.ok() if hasattr(ros,'rclpy') else True,recorder_node=ros.node.get_name() if hasattr(ros,'node') else None,recording=current['recording'],record_error=current['error'] or ros.error or (ros.recorder.error if ros.recorder else ''),
                       raw_topics=ros.inventory,odom_age=time.time()-ros.latest.get('odom_utc',0),
                       agent_utc=time.time(),control_available=bool(ros.status and time.time()-ros.status.get('utc',0)<.5))

    @app.post('/control/<op>')
    def control(op):
        if op not in ('arm','start','stop','beat','release'):raise ValueError('Unknown op')
        data=request.get_json() or {}
        if op=='start' and (not current['recording'] or data.get('run_id')!=current['run']):raise ValueError('Raw recording acknowledgement required')
        return jsonify(ros.request(op,**data))

    @app.post('/record/start')
    def record_start():
        with lock:
            rid=(request.get_json() or {}).get('run_id','')
            if not re.fullmatch(r'RUN_[A-Za-z0-9_-]{10,80}',rid):raise ValueError('Invalid run_id')
            if current['recording']:
                if current['run']==rid and not current['error']:
                    return jsonify(ok=True,run_id=rid,reused=True)
                raise ValueError('이전 실험 로그 기록이 아직 열려 있습니다: '+str(current['run']))
            if (root/rid).exists():raise ValueError('이미 사용한 실험 ID입니다. 새 실험으로 시작하세요: '+rid)
            path=root/rid;path.mkdir();ros.start_record(path)
            current.update(run=rid,recording=True,error='')
            return jsonify(ok=True,run_id=rid)

    @app.post('/record/stop')
    def record_stop():
        with lock:
            rid=(request.get_json() or {}).get('run_id')
            if not rid:raise ValueError('종료할 실험 ID가 필요합니다.')
            if current['recording'] and rid!=current['run']:raise ValueError('다른 실험의 기록을 종료할 수 없습니다.')
            if current['recording']:
                try:ros.stop_record()
                finally:current['recording']=False
            return jsonify(ok=True)

    @app.get('/archive/<rid>')
    def archive(rid):
        if current['recording']:raise ValueError('Close raw recording first')
        if not re.fullmatch(r'RUN_[A-Za-z0-9_-]{10,80}',rid) or not (root/rid).is_dir():raise ValueError('Unknown run')
        archive=root/(rid+'.zip')
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for p in (root/rid).rglob('*'):
                if p.is_file():z.write(p,p.relative_to(root/rid))
        return send_file(archive,as_attachment=True)

    def monitor():
        while True:
            error=ros.error or (ros.recorder.error if ros.recorder else '')
            if current['recording'] and error:
                current['error']=error
                try:ros.request('stop')
                except Exception:pass
            time.sleep(.1)
    threading.Thread(target=monitor,daemon=True).start()
    return app


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',default='jetson_experiments');p.add_argument('--port',type=int,default=18866)
    a=p.parse_args();create_agent(a.root,os.environ.get('D420_AGENT_TOKEN','')).run(host='127.0.0.1',port=a.port,threaded=True,use_reloader=False)

if __name__=='__main__':main()
