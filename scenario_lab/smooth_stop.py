"""Quintic velocity stop: continuous acceleration and jerk at both ends."""
import math

class SmoothStop:
 def __init__(self,speed,start,phase,hold,strength=100.):
  self.speed=max(0.,speed);self.start=start;self.phase=phase;self.hold=hold
  # max |dv/dt|=1.875*v/T; max |d2v/dt2|~=5.774*v/T^2.
  self.duration=max(.8,1.875*self.speed/.25,math.sqrt(5.774*self.speed/.6))
  self.duration=max(.1,self.duration*strength/100.)
  self.distance=self.speed*self.duration/2
 def velocity(self,now):
  u=max(0.,min(1.,(now-self.start)/self.duration))
  return self.speed*(1-(10*u**3-15*u**4+6*u**5))
 def acceleration(self,now):
  u=max(0.,min(1.,(now-self.start)/self.duration))
  return -self.speed/self.duration*(30*u*u-60*u**3+30*u**4)
