import math,unittest
from test_route_deadband import DeadbandSimulation
from test_l_route import obs
from engine import Pose,wrap
from depth_route import DepthRoute
from smooth_stop import SmoothStop
from experiment_cases import CASES,SPEEDS,parse_case

class CaseTests(unittest.TestCase):
 def test_all_fifteen_conditions_finish_same_route(self):
  for case in CASES:
   for speed in SPEEDS:
    with self.subTest(case=case,speed=speed):
     condition=parse_case(case,speed);s=DeadbandSimulation(speed=speed)
     s.e=DepthRoute();s.e.arm(3.2,2.2,speed,s.t,s.pose,obs(s.t),s.t,turn_extension=.3,smooth=condition['smooth'],experiment_case=condition)
     s.complete();self.assertEqual(s.e.state,'done',s.e.message)
     self.assertAlmostEqual(s.pose.x,3.5,delta=.1);self.assertAlmostEqual(s.pose.y,-2.2,delta=.12)
     self.assertLess(abs(wrap(s.pose.yaw+math.pi/2)),.05)
     smooths=[x for x in s.e.events if x['event']=='smooth_stop_started']
     self.assertEqual(len(smooths),3 if condition['smooth'] else 0)
     self.assertTrue(s.e.brake_done)
 def test_quintic_stop_has_bounded_deceleration_jerk_and_distance(self):
  for speed in SPEEDS:
   p=SmoothStop(speed,0,'depth_first','depth_brake');dt=.001
   self.assertEqual(p.acceleration(0),0);self.assertAlmostEqual(p.acceleration(p.duration),0)
   accel=[p.acceleration(i*dt) for i in range(int(p.duration/dt)+1)]
   self.assertLessEqual(max(abs(a) for a in accel),.251)
   self.assertLessEqual(max(abs((b-a)/dt) for a,b in zip(accel,accel[1:])),.601)
   distance=sum(p.velocity(i*dt)*dt for i in range(int(p.duration/dt)+1))
   self.assertAlmostEqual(distance,p.distance,delta=.001)
 def test_obstacle_overrides_scurve_and_replans_same_goal_after_clear(self):
  s=DeadbandSimulation(speed=.4);c=parse_case('combined',.4)
  s.e=DepthRoute();s.e.arm(3.2,2.2,.4,s.t,s.pose,obs(s.t),s.t,turn_extension=.3,smooth=True,experiment_case=c)
  for _ in range(1000):
   s.tick()
   if s.e.state=='depth_smooth_stop':break
  self.assertEqual(s.e.state,'depth_smooth_stop');self.assertEqual(s.tick(obs(s.t,points=[[.2,0.]])),(0.,0.))
  for _ in range(10):s.tick(obs(s.t,points=[[.2,0.]]))
  s.complete();self.assertEqual(s.e.state,'done',s.e.message);self.assertAlmostEqual(s.pose.x,3.5,delta=.1)
 def test_case_validation(self):
  for case,speed in [('missing',.2),('active',.41),('active',True)]:
   with self.assertRaises(ValueError):parse_case(case,speed)
 def test_small_cross_track_displacement_rejoins_original_route(self):
  s=DeadbandSimulation(speed=.3)
  for _ in range(30):s.tick()
  s.pose.y+=.1
  s.complete();self.assertEqual(s.e.state,'done',s.e.message)
  turn=next(row for row in s.rows if row[1]=='depth_turn')
  self.assertLess(abs(turn[3].y),.05)
 def test_scurve_sensor_pause_replans_without_changing_goal(self):
  s=DeadbandSimulation(speed=.3);c=parse_case('combined',.3)
  s.e=DepthRoute();s.e.arm(3.2,2.2,.3,s.t,s.pose,obs(s.t),s.t,turn_extension=.3,smooth=True,experiment_case=c)
  for _ in range(1000):
   s.tick()
   if s.e.state=='depth_smooth_stop':break
  for _ in range(8):s.tick(obs(s.t,ready=False))
  self.assertEqual(s.e.command,(0.,0.));self.assertIsNone(s.e.smooth_stop)
  s.complete();self.assertEqual(s.e.state,'done',s.e.message)
  self.assertAlmostEqual(s.pose.x,3.5,delta=.1)

if __name__=='__main__':unittest.main()
