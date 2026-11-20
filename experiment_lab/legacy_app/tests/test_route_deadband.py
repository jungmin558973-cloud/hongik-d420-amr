import math, unittest
from test_l_route import Simulation, obs
from engine import Pose, wrap

class DeadbandSimulation(Simulation):
    def tick(self, data=None):
        dt=.05;v,w=self.e.command;p=self.pose
        v=v if abs(v)>=.04 else 0.
        w=w if abs(w)>=.09 else 0.
        nv=p.v+max(-.6*dt,min(.3*dt,v-p.v))
        nw=p.w+max(-2*dt,min(2*dt,w-p.w))
        self.pose=Pose(p.x+nv*math.cos(p.yaw+nw*dt/2)*dt,p.y+nv*math.sin(p.yaw+nw*dt/2)*dt,wrap(p.yaw+nw*dt),nv,nw)
        self.t+=dt;self.e.observe(obs(self.t) if data is None else data,self.t)
        c=self.e.step(self.t,self.pose);self.rows.append((self.t,self.e.state,c,self.pose));return c

class DeadbandTests(unittest.TestCase):
    def test_turn_extension_adds_30cm_and_preserves_brake_position(self):
        from depth_route import DepthRoute
        s=DeadbandSimulation(first=.7,second=.6)
        s.e=DepthRoute();s.e.arm(.7,.6,.12,s.t,s.pose,obs(s.t),s.t,turn_extension=.3)
        s.complete();self.assertEqual(s.e.state,'done',s.e.message)
        turn=next(row for row in s.rows if row[1]=='depth_turn')
        self.assertAlmostEqual(turn[3].x,1.,delta=.04)
        self.assertAlmostEqual(s.e.brake_trigger_distance,.35,delta=.02)
        self.assertEqual(s.e.snapshot(s.t)['turn_extension_m'],.3)
    def test_l_finishes_with_motor_deadband(self):
        s=DeadbandSimulation(first=.7,second=.6);s.complete()
        self.assertEqual(s.e.state,'done',(s.e.message,s.pose))
        self.assertAlmostEqual(s.pose.x,.7,delta=.08)
        self.assertAlmostEqual(s.pose.y,-.6,delta=.08)
        self.assertLess(abs(wrap(s.pose.yaw+math.pi/2)),.05)

if __name__=='__main__':unittest.main()
