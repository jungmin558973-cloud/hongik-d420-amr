#!/usr/bin/env python3
"""Observe front depth in the robot frame. No motor publisher and no YOLO."""
import json,time,math,secrets
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.time import Time
from rclpy.duration import Duration
from rclpy.qos import QoSProfile,ReliabilityPolicy
from sensor_msgs.msg import Image,CameraInfo
from std_msgs.msg import String
from tf2_ros import Buffer,TransformListener
from camera_frame import stamp,image_array

class Observer(Node):
 def __init__(self):
  super().__init__('hongik_depth_observer');self.frame=None;self.info=None;self.last=None
  self.session=secrets.token_hex(8);self.sequence=0;self.frame_received=0.
  self.buffer=Buffer();self.listener=TransformListener(self.buffer,self)
  qos=QoSProfile(depth=1,reliability=ReliabilityPolicy.BEST_EFFORT)
  self.pub=self.create_publisher(String,'/d420/depth_route/observation',qos)
  self.create_subscription(Image,'/camera/camera/aligned_depth_to_color/image_raw',self.receive,qos)
  self.create_subscription(CameraInfo,'/camera/camera/color/camera_info',lambda m:setattr(self,'info',m),qos)
  self.create_timer(.1,self.observe)
 def receive(self,msg):self.frame=msg;self.frame_received=time.time()
 def observe(self):
  started=time.monotonic()
  f,i=self.frame,self.info;data={'version':1,'ready':False,'source_unix':stamp(f) if f else 0.,'published_unix':time.time(),'points':[]}
  try:
   if f is None or i is None:raise ValueError('정렬 깊이/카메라 보정 정보 대기')
   if not -.1<=time.time()-stamp(f)<=.25:raise ValueError('깊이 영상 지연')
   if stamp(f)==self.last:return
   self.last=stamp(f)
   if f.width!=i.width or f.height!=i.height or f.header.frame_id!=i.header.frame_id or i.k[0]<=0 or i.k[4]<=0:raise ValueError('깊이/보정 좌표 불일치')
   d=image_array(f);vs,us=np.mgrid[int(f.height*.1):int(f.height*.95):4,0:f.width:4];z=d[vs,us]
   valid=np.isfinite(z)&(z>.15)&(z<8.)
   fractions=[float(valid[:,k*valid.shape[1]//3:(k+1)*valid.shape[1]//3].mean()) for k in range(3)]
   data['valid_fractions']=fractions
   if min(fractions)<.55:raise ValueError('전방 깊이 유효 영역 부족')
   p=np.stack(((us-i.k[2])*z/i.k[0],(vs-i.k[5])*z/i.k[4],z),axis=-1)[valid]
   t=self.buffer.lookup_transform('base_footprint',f.header.frame_id,Time.from_msg(f.header.stamp),timeout=Duration(seconds=0.01)).transform
   q=t.rotation;x,y,zq,w=q.x,q.y,q.z,q.w
   r=np.array([[1-2*(y*y+zq*zq),2*(x*y-zq*w),2*(x*zq+y*w)],[2*(x*y+zq*w),1-2*(x*x+zq*zq),2*(y*zq-x*w)],[2*(x*zq-y*w),2*(y*zq+x*w),1-2*(x*x+y*y)]])
   p=p@r.T+np.array([t.translation.x,t.translation.y,t.translation.z])
   p=p[(p[:,2]>.05)&(p[:,2]<1.0)&(p[:,0]>0)&(p[:,0]<3)&(np.abs(p[:,1])<1.5)]
   points=np.unique(np.round(p[:,:2]/.03).astype(int),axis=0)*.03
   if len(points)>5000:raise ValueError('장애물 관측 처리량 초과')
   if time.time()-data['source_unix']>.3:raise ValueError('깊이 처리 지연')
   data.update(ready=True,points=points.round(3).tolist(),reason='')
  except Exception as e:
   data['reason']=str(e);data['fault']='calibration' if '보정 좌표 불일치' in str(e) else 'observation_unavailable'
  self.sequence+=1
  data.update(published_unix=time.time(),observer_session=self.session,sequence=self.sequence,
   processing_ms=round((time.monotonic()-started)*1000,3),frame_received_unix=self.frame_received)
  if data['source_unix']:
   data['frame_age_at_publish_ms']=round((data['published_unix']-data['source_unix'])*1000,3)
  msg=String();msg.data=json.dumps(data,allow_nan=False);self.pub.publish(msg)

def main():
 rclpy.init();n=Observer()
 try:rclpy.spin(n)
 except KeyboardInterrupt:pass
 finally:n.destroy_node();rclpy.shutdown()
if __name__=='__main__':main()
