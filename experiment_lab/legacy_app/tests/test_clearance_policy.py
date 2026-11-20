import unittest
from test_l_route import Simulation,obs
from depth_route import clear
class ClearanceTests(unittest.TestCase):
 def test_five_cm_static_gap_and_forward_braking_distance(self):
  self.assertFalse(clear([[.33,0]],0,0));self.assertTrue(clear([[.34,0]],0,0))
  self.assertTrue(clear([[.6,0]],.2,0));self.assertFalse(clear([[.6,0]],.4,0))
 def test_side_object_outside_body_and_five_cm_no_longitudinal_padding(self):
  self.assertTrue(clear([[.2,.30]],.4,0))
  self.assertFalse(clear([[.2,.24]],.4,0))
 def test_resume_on_next_tick_without_fixed_obstacle_dwell(self):
  s=Simulation();s.tick();self.assertEqual(s.tick(obs(s.t,points=[[.2,0]])),(0.,0.))
  self.assertGreater(s.tick()[0],0);self.assertTrue(s.e.active)
