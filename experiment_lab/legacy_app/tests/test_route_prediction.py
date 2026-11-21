import sys,math,json,types,unittest,tempfile,time
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'jetson'))
from engine import Pose,wrap
from depth_route import DepthRoute
from route_prediction import RoutePredictor,RoutePredictionConfig
from jetson.local_route_host import LocalRouteAdapter
from tray_protocol import TraySession,StatusParser
from route_tray_link import RouteTrayReceiver
from experiment_store import ExperimentRecorder

def observation(t):return dict(version=1,ready=True,source_unix=t,published_unix=t,points=[])
def ready_status():return dict(protocol=2,mode=4,predictive_mode=7,firmware='APPARENT_V4_FF',roll_enabled=True,pitch_enabled=True,imu_fresh=True,zero_saved=True,direction_checked=True,roll_deg=0.,pitch_deg=0.,roll_pwm=1500,pitch_pwm=1500,roll_home=1500,pitch_home=1500,app_timeout=False)
class FakeSerial:
 is_open=True;port='/dev/fake';in_waiting=0
 def __init__(self):self.writes=[];self.session=None
 def read(self,n):return b''
 def write(self,b):
  self.writes.append(b)
  if b'@T,' in b:self.session.latest['mode']=7
  elif b.strip()==b'h':self.session.latest['mode']=4
  return len(b)
def session():
 p=FakeSerial();s=TraySession(p);p.session=s;s.latest=ready_status();s.last_frame=1.;return s
class Message:
 def __init__(self,data=''):self.data=data
class Node:
 def __init__(self):self.pub_callbacks={};self.callbacks={};self.cmd_v=self.cmd_w=self.last_cmd_time=0.;self.duplicates=False
 def create_publisher(self,cls,topic,qos):
  return types.SimpleNamespace(publish=lambda msg:self.pub_callbacks.get(topic,lambda m:None)(msg))
 def create_subscription(self,cls,topic,fn,qos):self.callbacks[topic]=fn
 def count_publishers(self,topic):return 2 if self.duplicates and topic=='/d420/tray/status' else 1
 def count_subscribers(self,topic):return 1
class Harness:
 def __init__(self,protocol=2):
  self.t=1.;self.pose=Pose();self.serial=session();self.serial.latest['protocol']=protocol;self.receiver=RouteTrayReceiver(self.serial);self.node=Node()
  msg=types.ModuleType('std_msgs.msg');msg.String=Message
  qos=types.ModuleType('rclpy.qos');qos.QoSProfile=lambda **kw:kw;qos.ReliabilityPolicy=types.SimpleNamespace(BEST_EFFORT=1)
  self.temp=tempfile.TemporaryDirectory();self.recorder=ExperimentRecorder(Path(self.temp.name)/'test.sqlite3')
  for _ in range(200):
   if self.recorder.ready or self.recorder.error:break
   time.sleep(.005)
  with patch.dict(sys.modules,{'std_msgs.msg':msg,'rclpy.qos':qos}):self.a=LocalRouteAdapter(self.node,self.recorder)
  self.targets=[];self.commands=[]
  def target(msg):
   d=json.loads(msg.data);self.targets.append((self.t,self.pose.x,d))
   self.receiver.receive(d,self.t,self.t)
  self.node.pub_callbacks['/d420/tray/route_target']=target
  self.clock=patch('jetson.local_route_host.time.monotonic',lambda:self.t);self.wall=patch('jetson.local_route_host.time.time',lambda:self.t)
 def __enter__(self):self.clock.start();self.wall.start();return self
 def __exit__(self,*args):self.wall.stop();self.clock.stop();self.recorder.close();self.temp.cleanup()
 def status(self):
  self.serial.last_frame=self.t
  d=self.serial.status(self.t,self.t);d['route_control']=self.receiver.status(self.t)
  self.a.tray_status(Message(json.dumps(d)))
 def start(self):
  self.a.observe(Message(json.dumps(observation(self.t))));self.status();self.a.live=True
  d=dict(op='start',request_id='start-request-123',route_id='route-test-123456789',created_unix=self.t,bridge_session=self.a.host.session,first=3.2,second=2.2,speed=.12,trial=False,brake=True,tray_predictive=True,tray_prediction={})
  self.a.request(Message(json.dumps(d)));return self.a.host.reply
 def tick(self,live=True):
  self.t+=.05;p=self.pose;v,w=self.commands[-1] if self.commands and self.commands[-1] else (0.,0.)
  nv=p.v+max(-.03,min(.015,v-p.v));nw=p.w+max(-.1,min(.1,w-p.w))
  self.pose=Pose(p.x+nv*math.cos(p.yaw+nw*.025)*.05,p.y+nv*math.sin(p.yaw+nw*.025)*.05,wrap(p.yaw+nw*.05),nv,nw)
  self.status();self.a.observe(Message(json.dumps(observation(self.t))))
  c=self.a.tick(self.t,live,self.pose.x,self.pose.y,types.SimpleNamespace(yaw=self.pose.yaw,v=(self.pose.v,0,self.pose.w)))
  self.commands.append(c);self.receiver.tick(self.t,self.t);self.serial.tick(self.t,self.t);return c

class PredictionTests(unittest.TestCase):
 def test_route_requests_are_idempotent_and_full_route_is_durable(self):
  from experiment_store import read_experiments
  with Harness() as h:
   self.assertTrue(h.start()['accepted']);self.assertTrue(h.start()['accepted'])
   for _ in range(2000):
    h.tick()
    if not h.a.host.engine.active:break
   self.assertEqual(h.a.host.engine.state,'done');h.recorder.close()
   records=read_experiments(h.recorder.path);self.assertEqual(len(records),1)
   record=read_experiments(h.recorder.path,records[0]['run_id'])
   self.assertEqual(record['status'],'completed');self.assertEqual(record['sample_count'],h.a.record_seq)
   self.assertTrue(any(r['phase']=='depth_turn' for r in record['samples']))
   self.assertTrue(any(r['phase']=='depth_second' for r in record['samples']))
   self.assertEqual(record['samples'][-1]['cmd_v_m_s'],0)
 def test_storage_error_stops_active_route(self):
  with Harness() as h:
   h.start();h.tick();h.recorder.error='simulated disk failure'
   self.assertEqual(h.tick(),(0.,0.));self.assertFalse(h.a.host.engine.active)
 def test_full_local_route_to_usb_pretilts_before_brake_then_returns_level(self):
  with Harness() as h:
   self.assertTrue(h.start()['accepted']);self.assertEqual(h.tick(),(0.,0.));self.assertFalse(h.a.target_acquired)
   for _ in range(2000):
    h.tick()
    if not h.a.host.engine.active:break
   e=h.a.host.engine;self.assertEqual(e.state,'done',e.message);self.assertTrue(h.a.target_acquired)
   negatives=[(t,x) for t,x,d in h.targets if d['op']=='target' and float(d['command'].strip().split(',')[2])<-.1]
   self.assertTrue(negatives);self.assertLess(negatives[0][1],e.brake_trigger_distance)
   self.assertTrue(any(b'@T,' in b for b in h.serial.port.writes));self.assertEqual(h.targets[-1][2]['op'],'release')
   h.serial.tick(h.t+.05,h.t+.05);self.assertEqual(h.serial.latest['mode'],4)
   self.assertIsNone(h.tick(),'completed route must not block later manual driving')
 def test_protocol_one_cannot_start_predictive_route(self):
  with Harness(protocol=1) as h:self.assertFalse(h.start()['accepted']);self.assertIsNone(h.a.host.engine)
 def test_lost_imu_stops_chassis_without_waiting_for_usb(self):
  with Harness() as h:
   h.start();h.tick();h.tick();h.serial.latest['imu_fresh']=False
   self.assertEqual(h.tick(),(0.,0.));self.assertFalse(h.a.host.engine.active);self.assertEqual(h.targets[-1][2]['op'],'release')
 def test_disabled_firmware_or_duplicate_status_stops(self):
  for fault in ('mode','duplicate'):
   with Harness() as h:
    h.start();h.tick();h.tick()
    if fault=='mode':h.serial.latest['mode']=0
    else:h.node.duplicates=True
    self.assertEqual(h.tick(),(0.,0.));self.assertFalse(h.a.host.engine.active)
 def test_missing_actual_firmware_mode_prevents_motion(self):
  with Harness() as h:
   h.start();h.serial.port.write=lambda b:len(b)
   for _ in range(35):self.assertIn(h.tick(),((0.,0.),None))
   self.assertFalse(h.a.host.engine.active);self.assertEqual(h.pose.x,0)
 def test_model_limits_axes_and_slope_zero_target(self):
  e=DepthRoute();e.arm(3.2,2.2,.12,1,Pose(),observation(1),1)
  e.command=(.12,0.);p=RoutePredictor(RoutePredictionConfig(angle_limit=2,pitch_sign=-1))
  pose=Pose(x=1.59,v=.12)
  for i in range(20):r,angle=p.step(e,pose,1+i*.05);self.assertLessEqual(abs(angle),2)
  self.assertGreater(angle,0);self.assertEqual(p.diagnostics['source'],'PLANNED_BRAKE_PREVIEW')
  e.brake_done=True;e.command=(.12,0.)
  for i in range(100):p.step(e,Pose(x=2,v=.12),2+i*.05)
  self.assertAlmostEqual(p.pitch,0,places=5);self.assertFalse(p.diagnostics['vehicle_imu_used'])
 def test_invalid_prediction_configuration(self):
  for d in [dict(lead=0),dict(angle_limit=8),dict(brake_seconds=float('nan')),dict(pitch_sign=True),dict(swap_axes=1)]:
   with self.assertRaises(ValueError):RoutePredictionConfig.parse(d)
 def test_receiver_expiry_replay_and_manual_override(self):
  s=session();r=RouteTrayReceiver(s);lease='lease-test-123456789'
  def frame(seq,stamp=1,op='target'):return dict(op=op,route_id=lease,seq=seq,issued_at=stamp,command='@T,0.000,-1.000\n')
  r.receive(frame(1),1,1);s.tick(1,1)
  with self.assertRaises(ValueError):r.receive(frame(1),1.1,1.1)
  with self.assertRaises(ValueError):r.receive(frame(2,stamp=0),1.1,1.1)
  r.tick(1.31,1.31);self.assertFalse(r.status(1.31)['active']);s.tick(1.31,1.31);self.assertEqual(s.latest['mode'],4)
  with self.assertRaises(ValueError):r.receive(frame(3,stamp=1.4),1.4,1.4)
  s=session();r=RouteTrayReceiver(s);r.receive(frame(1),1,1)
  s.request(dict(command='x',issued_at=1.1,nonce='manual-override'),1.1,1.1);r.revoke('manual',1.1,1.1)
  s.tick(1.1,1.1);self.assertEqual(s.port.writes[0],b'\nx');self.assertIsNone(s.target)
  with self.assertRaises(ValueError):r.receive(frame(4,stamp=1.2),1.2,1.2)
 def test_mode_seven_frame_parses_as_actual_feedback(self):
  lines=['-------------------------','ROLL=1500 (ON)','PITCH=1500 (ON)','MODE=7','PROTO=2','FIRMWARE=APPARENT_V4_FF','PREDICT_MODE=7','TARGET R=0.000 P=-1.500','APP_TIMEOUT=0','IMU R=0.00 deg | P=-1.20 deg','HOME R=1500 P=1500','ZERO=1 DIR=1','-------------------------']
  frames=StatusParser().feed(('\n'.join(lines)+'\n').encode());self.assertEqual(frames[0]['mode'],7);self.assertEqual(frames[0]['target_pitch_deg'],-1.5)
 def test_level_only_route_works_with_legacy_protocol_and_lost_level_stops(self):
  with Harness(protocol=1) as h:
   h.a.observe(Message(json.dumps(observation(h.t))));h.status();h.a.live=True
   d=dict(op='start',request_id='level-start-123',route_id='level-route-123456789',created_unix=h.t,bridge_session=h.a.host.session,first=.5,second=.5,speed=.12,trial=False,brake=True,tray_level=True,tray_predictive=False)
   h.a.request(Message(json.dumps(d)));self.assertTrue(h.a.host.reply['accepted'])
   self.assertGreater(h.tick()[0],0);self.assertEqual(h.targets,[])
   h.serial.latest['mode']=0;self.assertEqual(h.tick(),(0.,0.));self.assertFalse(h.a.host.engine.active)

if __name__=='__main__':unittest.main()
