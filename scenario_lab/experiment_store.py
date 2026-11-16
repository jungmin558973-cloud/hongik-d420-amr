"""SQLite experiment archive. Disk writes run off the motor/ROS timer thread."""
import json, math, queue, sqlite3, threading, time, uuid
from contextlib import closing
from pathlib import Path

SCHEMA='''
CREATE TABLE IF NOT EXISTS experiments(
 run_id TEXT PRIMARY KEY, owner TEXT, kind TEXT, mode TEXT, started_unix REAL,
 ended_unix REAL, status TEXT, reason TEXT, config_json TEXT, summary_json TEXT,
 sample_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS samples(
 run_id TEXT NOT NULL REFERENCES experiments(run_id), seq INTEGER NOT NULL,
 unix_s REAL, monotonic_s REAL, elapsed_s REAL, phase TEXT,
 cmd_v_m_s REAL, cmd_w_rad_s REAL, measured_v_m_s REAL, measured_w_rad_s REAL,
 estimated_ax_m_s2 REAL, estimated_alpha_rad_s2 REAL,
 tray_roll_deg REAL, tray_pitch_deg REAL, target_roll_deg REAL, target_pitch_deg REAL,
 x_m REAL,y_m REAL,yaw_rad REAL,cmd_ax_m_s2 REAL,cmd_alpha_rad_s2 REAL,cmd_jerk_m_s3 REAL,
 roll_pwm_us REAL,pitch_pwm_us REAL,esp_target_roll_deg REAL,esp_target_pitch_deg REAL,
 esp_mode INTEGER,esp_frame_unix REAL,tray_new_sample INTEGER,tray_valid INTEGER,
 data_json TEXT NOT NULL, PRIMARY KEY(run_id,seq));
CREATE INDEX IF NOT EXISTS experiments_time ON experiments(started_unix DESC);
'''
FIELDS=('unix_s','monotonic_s','elapsed_s','phase','cmd_v_m_s','cmd_w_rad_s',
        'measured_v_m_s','measured_w_rad_s','estimated_ax_m_s2','estimated_alpha_rad_s2',
        'tray_roll_deg','tray_pitch_deg','target_roll_deg','target_pitch_deg',
        'x_m','y_m','yaw_rad','cmd_ax_m_s2','cmd_alpha_rad_s2','cmd_jerk_m_s3','roll_pwm_us','pitch_pwm_us',
        'esp_target_roll_deg','esp_target_pitch_deg','esp_mode','esp_frame_unix','tray_new_sample','tray_valid')

def default_path():
    return Path(__file__).resolve().parent/'runs'/'experiments.sqlite3'

def encode(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))

class ExperimentRecorder:
    def __init__(self,path=None,owner='local-route'):
        self.path=Path(path or default_path());self.owner=owner
        self.queue=queue.Queue(maxsize=2048);self.ready=False;self.error='';self.closed=False
        self.thread=threading.Thread(target=self._worker,daemon=True,name='experiment-sqlite')
        self.thread.start()

    def _put(self,item):
        if self.error or self.closed:raise ValueError('실험 DB 저장 불가: '+(self.error or '종료됨'))
        try:self.queue.put_nowait(item)
        except queue.Full:
            self.error='저장 대기열 초과';raise ValueError('실험 DB 저장 대기열 초과')

    def begin(self,kind,mode,config,run_id=None,wall=None):
        if not self.ready:raise ValueError('실험 DB 준비 중: '+self.error)
        rid=run_id or uuid.uuid4().hex
        self._put(('begin',(rid,self.owner,kind,mode,time.time() if wall is None else wall,encode(config))))
        return rid

    def sample(self,rid,seq,row):
        self._put(('sample',(rid,seq,*[row.get(k) for k in FIELDS],encode(row))))

    def finish(self,rid,summary,wall=None):
        self._put(('finish',(time.time() if wall is None else wall,
            'completed' if summary.get('state')=='done' else 'stopped',summary.get('message',''),encode(summary),rid)))

    def _worker(self):
        connection=None
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            connection=sqlite3.connect(self.path,timeout=3)
            connection.execute('PRAGMA journal_mode=WAL');connection.execute('PRAGMA synchronous=FULL')
            connection.execute('PRAGMA foreign_keys=ON');connection.executescript(SCHEMA)
            columns={row[1] for row in connection.execute('PRAGMA table_info(samples)')}
            for column in FIELDS:
                if column not in columns:connection.execute('ALTER TABLE samples ADD COLUMN '+column+(' TEXT' if column=='phase' else ' REAL'))
            connection.execute("UPDATE experiments SET status='interrupted',reason='기록 프로세스 재시작 · 종료 확인 없음',ended_unix=? WHERE owner=? AND status='running'",(time.time(),self.owner))
            connection.commit();self.ready=True;last=time.monotonic()
            while True:
                try:item=self.queue.get(timeout=.25)
                except queue.Empty:
                    connection.commit();last=time.monotonic();continue
                try:
                    op,args=item
                    if op=='close':connection.commit();return
                    if op=='begin':
                        connection.execute("INSERT INTO experiments(run_id,owner,kind,mode,started_unix,config_json,status) VALUES(?,?,?,?,?,?,'running')",args)
                    elif op=='sample':
                        columns='run_id,seq,'+','.join(FIELDS)+',data_json'
                        connection.execute('INSERT INTO samples('+columns+') VALUES('+','.join('?' for _ in args)+')',args)
                        connection.execute('UPDATE experiments SET sample_count=sample_count+1 WHERE run_id=?',(args[0],))
                    elif op=='finish':
                        ended,status,reason,summary_json,rid=args
                        summary=json.loads(summary_json)
                        metrics=connection.execute('SELECT MAX(ABS(tray_roll_deg)),MAX(ABS(tray_pitch_deg)),AVG(tray_roll_deg*tray_roll_deg),AVG(tray_pitch_deg*tray_pitch_deg),MAX(ABS(measured_v_m_s)),MIN(estimated_ax_m_s2),MAX(ABS(cmd_jerk_m_s3)),SUM(tray_new_sample) FROM samples WHERE run_id=?',(rid,)).fetchone()
                        names=('observed_peak_roll_deg','observed_peak_pitch_deg','roll_rms_deg','pitch_rms_deg','achieved_peak_speed_m_s','minimum_estimated_acceleration_m_s2','peak_command_jerk_m_s3','esp32_new_frames')
                        values=list(metrics)
                        for index in (2,3):values[index]=math.sqrt(values[index]) if values[index] is not None else None
                        summary['experiment_metrics']=dict(zip(names,values))
                        args=(ended,status,reason,encode(summary),rid)
                        connection.execute('UPDATE experiments SET ended_unix=?,status=?,reason=?,summary_json=? WHERE run_id=?',args)
                    if op in ('begin','finish') or time.monotonic()-last>=.25:
                        connection.commit();last=time.monotonic()
                finally:self.queue.task_done()
        except Exception as exc:
            self.error=type(exc).__name__+': '+str(exc);self.ready=False
        finally:
            if connection:connection.close()

    def close(self):
        if self.closed:return
        if self.thread.is_alive():
            try:self.queue.put(('close',None),timeout=2)
            except queue.Full:self.error='저장 종료 대기열 초과'
        self.closed=True;self.thread.join(timeout=5)
        if self.thread.is_alive():self.error='저장 종료 시간 초과'

    def status(self):
        return {'ready':self.ready and not self.closed and not self.error,'error':self.error,
                'pending':self.queue.qsize(),'path':str(self.path)}

class SampleBuilder:
    def __init__(self):self.previous=None;self.previous_command=None;self.previous_command_acceleration=None;self.tray_key=None
    def build(self,now,wall,elapsed,phase,command,pose,tray=None,prediction=None,live=True,feedback_at=None,**extra):
        tray=tray or {};prediction=prediction or {}
        age=tray.get('status_age_s');stamp=tray.get('stamp_unix')
        if type(age) in (int,float) and type(stamp) in (int,float):age+=max(0,wall-stamp)
        valid=bool(tray.get('connected') and tray.get('imu_fresh') and
                   type(age) in (int,float) and 0<=age<=.9)
        key=(tray.get('monitor_session'),tray.get('frame_sequence'))
        new_tray=valid and key!=(None,None) and key!=self.tray_key
        if new_tray:self.tray_key=key
        ax=alpha=None;source=now if feedback_at is None else feedback_at
        new_feedback=bool(live and (self.previous is None or source>self.previous[0]))
        if new_feedback:
            if self.previous:
                t,v,w=self.previous;dt=source-t
                if .001<=dt<=.5:ax=(pose.v-v)/dt;alpha=(pose.w-w)/dt
            self.previous=(source,pose.v,pose.w)
        if not live:self.previous=None
        cmd_ax=cmd_alpha=cmd_jerk=None
        if self.previous_command:
            t,v,w=self.previous_command;dt=now-t
            if .001<=dt<=.5:cmd_ax=(command[0]-v)/dt;cmd_alpha=(command[1]-w)/dt
            if cmd_ax is not None and self.previous_command_acceleration is not None:cmd_jerk=(cmd_ax-self.previous_command_acceleration)/dt
        self.previous_command_acceleration=cmd_ax
        self.previous_command=(now,*command)
        return dict(unix_s=wall,monotonic_s=now,elapsed_s=elapsed,phase=phase,
            cmd_v_m_s=command[0],cmd_w_rad_s=command[1],
            measured_v_m_s=pose.v if live else None,measured_w_rad_s=pose.w if live else None,
            estimated_ax_m_s2=ax,estimated_alpha_rad_s2=alpha,
            x_m=pose.x,y_m=pose.y,yaw_rad=pose.yaw,cmd_ax_m_s2=cmd_ax,cmd_alpha_rad_s2=cmd_alpha,cmd_jerk_m_s3=cmd_jerk,
            roll_pwm_us=tray.get('roll_pwm'),pitch_pwm_us=tray.get('pitch_pwm'),
            esp_target_roll_deg=tray.get('target_roll_deg'),esp_target_pitch_deg=tray.get('target_pitch_deg'),
            esp_mode=tray.get('mode'),esp_frame_unix=tray.get('sample_unix'),
            acceleration_source='velocity_difference_not_vehicle_imu',feedback_source_monotonic=source,
            feedback_new_sample=new_feedback,pose={'x_m':pose.x,'y_m':pose.y,'yaw_rad':pose.yaw},
            tray_valid=valid,tray_new_sample=new_tray,tray_status=tray,
            tray_roll_deg=tray.get('roll_deg') if valid else None,tray_pitch_deg=tray.get('pitch_deg') if valid else None,
            target_roll_deg=prediction.get('roll_deg'),target_pitch_deg=prediction.get('pitch_deg'),
            target_source='requested_not_servo_position',motor_tx_confirmed=None,
            prediction=prediction,**extra)

def read_experiments(path=None,run_id=None):
    path=Path(path or default_path())
    if not path.exists():return None if run_id else []
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
        db.row_factory=sqlite3.Row
        db.execute('BEGIN')
        if run_id:
            row=db.execute('SELECT * FROM experiments WHERE run_id=?',(run_id,)).fetchone()
            if row is None:return None
            result=dict(row)
            result['samples']=[dict(x) for x in db.execute('SELECT * FROM samples WHERE run_id=? ORDER BY seq',(run_id,))]
            return result
        return [dict(x) for x in db.execute('SELECT * FROM experiments ORDER BY started_unix DESC LIMIT 100')]
