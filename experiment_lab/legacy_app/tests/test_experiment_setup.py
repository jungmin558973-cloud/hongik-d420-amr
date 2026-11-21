import unittest,math
from test_route_deadband import DeadbandSimulation
from test_l_route import obs
from depth_route import DepthRoute
from smooth_stop import SmoothStop
from experiment_options import parse_options

class SetupTests(unittest.TestCase):
 def test_160cm_brake_only_records_actual_stop_and_calibration(self):
  for speed in (.2,.3,.4):
   s=DeadbandSimulation(speed=speed);s.e=DepthRoute();s.e.arm(3.2,2.2,speed,s.t,s.pose,obs(s.t),s.t,options={'deceleration':.6,'latency':0.})
   s.complete();self.assertEqual(s.e.state,'done',s.e.message)
   self.assertFalse(any(x['event']=='depth_turn_started' for x in s.e.events))
   self.assertLess(abs(s.pose.x-1.6),.07);self.assertIsNotNone(s.e.brake_calibration());self.assertLess(s.e.brake_trigger_along,1.6)
 def test_scurve_strength_changes_real_profile_duration(self):
  durations=[SmoothStop(.4,0,'first','hold',p).duration for p in (1,25,50,100)]
  self.assertEqual(durations,sorted(durations));self.assertLess(durations[0],durations[-1]/10)
  for strength in (25,50,100):
   s=DeadbandSimulation(speed=.3);s.e=DepthRoute();s.e.arm(3.2,2.2,.3,s.t,s.pose,obs(s.t),s.t,options={'smooth_pct':strength})
   s.complete();self.assertEqual(s.e.state,'done',s.e.message);self.assertTrue(s.e.smooth_enabled);self.assertIsNone(s.e.brake_calibration())
 def test_fast_turn_uses_larger_command_and_settles(self):
  s=DeadbandSimulation(first=.7,second=.6);s.complete()
  self.assertEqual(s.e.state,'done',s.e.message)
  self.assertGreater(max(abs(c[1]) for _,_,c,_ in s.rows),.4)
  self.assertLess(abs(s.pose.yaw+math.pi/2),.05)
 def test_invalid_setup_rejected(self):
  for d in ({'smooth_pct':101},{'turn_rate':5.4},{'deceleration':0},{'line_m':2.},{'reuse_calibration':'yes'}):
   with self.assertRaises(ValueError):parse_options(d)
