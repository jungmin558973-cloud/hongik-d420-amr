import json, sqlite3, tempfile, time, unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import Pose
from experiment_store import ExperimentRecorder,SampleBuilder,read_experiments

def wait_ready(rec):
    for _ in range(200):
        if rec.ready or rec.error:return
        time.sleep(.005)
    raise AssertionError('DB initialization timeout')

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'experiments.sqlite3'
        self.r=ExperimentRecorder(self.path);wait_ready(self.r)
    def tearDown(self):self.r.close();self.temp.cleanup()
    def test_acceleration_uses_distinct_velocity_frames_and_preserves_raw_signals(self):
        b=SampleBuilder();tray=dict(connected=True,imu_fresh=True,status_age_s=0,stamp_unix=1,monitor_session='esp',frame_sequence=1,roll_deg=2,pitch_deg=-1,roll_pwm=1501,pitch_pwm=1499)
        first=b.build(1,1,0,'first',(.1,0),Pose(v=.1),tray,feedback_at=1)
        duplicate=b.build(1.05,1.05,.05,'first',(.1,0),Pose(v=.1),tray,feedback_at=1)
        changed=b.build(1.1,1.1,.1,'brake',(0,0),Pose(v=.05),tray,feedback_at=1.1)
        self.assertTrue(first['tray_new_sample']);self.assertFalse(duplicate['tray_new_sample'])
        self.assertIsNone(duplicate['estimated_ax_m_s2']);self.assertAlmostEqual(changed['estimated_ax_m_s2'],-.5)
        self.assertAlmostEqual(changed['cmd_ax_m_s2'],-2);self.assertEqual(changed['roll_pwm_us'],1501)
        stale=b.build(3,3,2,'stop',(0,0),Pose(),tray,live=False)
        self.assertFalse(stale['tray_valid']);self.assertIsNone(stale['tray_roll_deg']);self.assertIsNone(stale['measured_v_m_s'])
    def test_two_runs_persist_all_samples_and_terminal_reason(self):
        b=SampleBuilder()
        for k in range(2):
            rid=self.r.begin('depth-route','real',{'first':.7},wall=10+k)
            for i in range(20):
                self.r.sample(rid,i+1,b.build(i*.05+1,10+i*.05,i*.05,'depth_first',(.12,0),Pose(v=.1)))
            self.r.finish(rid,{'state':'stopped','message':'실제 정지 사유','stop_context':{'phase':'depth_first'}})
        self.r.close();records=read_experiments(self.path);self.assertEqual(len(records),2)
        for r in records:
            data=read_experiments(self.path,r['run_id']);self.assertEqual(data['sample_count'],20)
            self.assertEqual(len(data['samples']),20);self.assertEqual(data['reason'],'실제 정지 사유')
            self.assertEqual(json.loads(data['samples'][0]['data_json'])['cmd_v_m_s'],.12)
        db=sqlite3.connect(self.path)
        try:self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        finally:db.close()
    def test_process_restart_marks_unfinished_only_for_same_owner(self):
        rid=self.r.begin('depth-route','real',{});self.r.close()
        self.r=ExperimentRecorder(self.path,owner='web-experiments');wait_ready(self.r)
        self.assertEqual(read_experiments(self.path,rid)['status'],'running');self.r.close()
        self.r=ExperimentRecorder(self.path);wait_ready(self.r)
        self.assertEqual(read_experiments(self.path,rid)['status'],'interrupted')

if __name__=='__main__':unittest.main()
