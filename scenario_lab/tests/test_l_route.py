import sys,math,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import Pose,wrap
from depth_route import DepthRoute
from jetson.local_route_host import RouteHost

def obs(now,ready=True,points=None):
 return dict(version=1,ready=ready,source_unix=now,published_unix=now,points=points or [],observer_session='cam',reason='영상 지연',fault='transport_stale')

class Simulation:
 def __init__(self,brake=True,speed=.12,first=3.2,second=2.2):
  self.t=1.;self.pose=Pose();self.e=DepthRoute();self.e.arm(first,second,speed,self.t,self.pose,obs(self.t),self.t,brake=brake);self.rows=[]
 def tick(self,data=None):
  dt=.05;v,w=self.e.command;p=self.pose
  # Bounded inertia: zero command does not imply an instantly stationary robot.
  nv=p.v+max(-.6*dt,min(.3*dt,v-p.v));nw=p.w+max(-2*dt,min(2*dt,w-p.w))
  self.pose=Pose(p.x+nv*math.cos(p.yaw+nw*dt/2)*dt,p.y+nv*math.sin(p.yaw+nw*dt/2)*dt,wrap(p.yaw+nw*dt),nv,nw)
  self.t+=dt;d=obs(self.t) if data is None else data
  self.e.observe(d,self.t);c=self.e.step(self.t,self.pose);self.rows.append((self.t,self.e.state,c,self.pose))
  return c
 def complete(self):
  for _ in range(18000):
   self.tick()
   if not self.e.active:return
  raise AssertionError('simulation did not finish')

class RouteTests(unittest.TestCase):
 def test_full_l_once_brake_and_stopped_arrival(self):
  s=Simulation();s.complete();e=s.e
  self.assertEqual(e.state,'done',e.message);self.assertTrue(e.brake_done)
  self.assertEqual(sum(x['event']=='depth_brake' for x in e.events),1)
  self.assertAlmostEqual(e.brake_trigger_distance,1.6,delta=.02)
  self.assertGreater(e.brake_end_distance,e.brake_trigger_distance)
  self.assertAlmostEqual(s.pose.x,3.2,delta=.12);self.assertAlmostEqual(s.pose.y,-2.2,delta=.12)
  self.assertLess(abs(wrap(s.pose.yaw+math.pi/2)),.05);self.assertLess(abs(s.pose.v),.015)
  start=next(x['t'] for x in e.events if x['event']=='depth_brake')
  end=next(x['t'] for x in e.events if x['event']=='depth_brake_confirmed')
  self.assertGreater(end-start,1.0)
  self.assertTrue(all(c==(0.,0.) for _,state,c,_ in s.rows if state in ('depth_brake','depth_turn_settle','depth_finish')))
 def test_optional_brake_off_and_rotated_origin(self):
  s=Simulation(False,first=.7,second=.6);s.e.origin.yaw=s.e.target_yaw=s.e.segment_origin.yaw=math.pi-.1;s.pose.yaw=math.pi-.1;s.e.pose.yaw=s.pose.yaw
  s.complete();self.assertEqual(s.e.state,'done');self.assertFalse(s.e.brake_done)
 def test_stale_depth_zero_then_distinct_frame_recovery(self):
  s=Simulation(first=1,second=1)
  for _ in range(20):s.tick()
  self.assertEqual(s.tick(obs(s.t-1)),(0.,0.));self.assertIsNotNone(s.e.pause_at)
  for _ in range(12):s.tick()
  self.assertIsNone(s.e.pause_at);self.assertTrue(s.e.active)
  s.complete();self.assertEqual(s.e.state,'done');self.assertEqual(sum(x['event']=='depth_brake' for x in s.e.events),1)
 def test_long_depth_loss_and_explicit_stop_never_restart(self):
  s=Simulation()
  for _ in range(70):s.tick(obs(s.t,ready=False))
  self.assertFalse(s.e.active);self.assertIn('복구 시간 초과',s.e.message)
  for _ in range(10):self.assertEqual(s.tick(),(0.,0.))
  s=Simulation();s.e.stop('사용자 정지');self.assertEqual(s.tick(),(0.,0.));self.assertFalse(s.e.active)
 def test_obstacle_blocks_motion_and_wait_expires(self):
  s=Simulation()
  for _ in range(330):self.assertEqual(s.tick(obs(s.t,points=[[.2,0.]])),(0.,0.))
  self.assertFalse(s.e.active);self.assertIn('장애물',s.e.message)
 def test_odometry_fault_stops(self):
  s=Simulation();s.pose.x=1.;self.assertEqual(s.tick(),(0.,0.));self.assertFalse(s.e.active)
 def test_brake_stop_confirmation_timeout(self):
  s=Simulation();e=s.e;e.enter_hold('depth_brake',s.t)
  for _ in range(170):
   s.t+=.05;e.observe(obs(s.t),s.t);e.step(s.t,Pose(v=.02))
  self.assertFalse(e.active);self.assertIn('정지 확인 시간 초과',e.message)
 def test_validation_and_slow_route_budget(self):
  for values in [(True,1,.12),(1,1,float('nan')),(1,1,.4)]:
   with self.assertRaises(ValueError):DepthRoute().arm(*values,1,Pose(),obs(1),1)
  with self.assertRaises(ValueError):DepthRoute().arm(1,1,.12,1,Pose(),obs(1),1,brake='yes')
  s=Simulation(speed=.03,first=4,second=4);self.assertGreater(s.e.route_timeout,8/.03)
 def test_host_trial_disables_mid_brake_and_bounds_trial(self):
  h=RouteHost();now=1;h.observation(obs(now),now)
  h.request(dict(request_id='request-123',route_id='route-123456789012',op='start',created_unix=now,bridge_session=h.session,first=3.2,second=2.2,speed=.12,trial=True,brake=True),Pose(),True,now,now)
  self.assertTrue(h.reply['accepted']);self.assertFalse(h.engine.brake_enabled)
  self.assertEqual(h.tick(Pose(),True,11.1,11.1),(0.,0.));self.assertFalse(h.engine.active)
 def test_host_control_gap_pauses_and_feedback_loss_stops(self):
  h=RouteHost();h.observation(obs(1),1)
  h.request(dict(request_id='request-123',route_id='route-123456789012',op='start',created_unix=1,bridge_session=h.session,first=1,second=1,speed=.12,trial=False),Pose(),True,1,1)
  self.assertEqual(h.tick(Pose(),True,1.2,1.2),(0.,0.));self.assertIsNotNone(h.engine.pause_at)
  self.assertEqual(h.tick(Pose(),False,1.25,1.25),(0.,0.));self.assertFalse(h.engine.active)
 def test_full_route_with_periodic_depth_outages(self):
  s=Simulation(first=1.5,second=1.0)
  for i in range(10000):
   # 0.5s unavailable bursts, including around phase transitions.
   s.tick(obs(s.t,ready=False) if i%160 in range(100,110) else None)
   if not s.e.active:break
  self.assertEqual(s.e.state,'done',s.e.message);self.assertGreater(s.e.pause_count,2)
  self.assertEqual(sum(x['event']=='depth_brake' for x in s.e.events),1)

if __name__=='__main__':unittest.main()
