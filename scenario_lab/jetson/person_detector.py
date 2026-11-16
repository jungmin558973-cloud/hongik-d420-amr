#!/usr/bin/env python3
"""YOLO + aligned D435 depth observer. NEVER publishes motor commands."""
import argparse
from collections import deque
import json
import math
import threading
import time
import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image, CameraInfo
from std_msgs.msg import String
from yolo_runtime import Yolo
from person_observation import Identity, measure_depth, appearance_signature
from camera_frame import stamp, image_array




class Detector(Node):
    def __init__(self, engine):
        super().__init__('hongik_person_detector')
        self.yolo = Yolo(engine)
        self.identity = Identity()
        self.lock = threading.Lock()
        self.color = self.info = None
        self.depth = deque(maxlen=8)
        self.last_processed = None
        self.pub = self.create_publisher(String,'/d420/follow/observation',10)
        latest=QoSProfile(depth=1,reliability=ReliabilityPolicy.BEST_EFFORT)
        self.create_subscription(Image,'/camera/camera/color/image_raw',self.rgb,latest)
        self.create_subscription(Image,'/camera/camera/aligned_depth_to_color/image_raw',self.dep,latest)
        self.create_subscription(CameraInfo,'/camera/camera/color/camera_info',self.camera_info,qos_profile_sensor_data)
        self.create_timer(.05,self.process)

    def rgb(self,msg): self.color = msg
    def dep(self,msg): self.depth.append(msg)
    def camera_info(self,msg): self.info = msg

    def emit(self,data):
        msg = String()
        msg.data = json.dumps({'version':1,'published_unix':time.time(),**data},allow_nan=False)
        self.pub.publish(msg)

    def process(self):
        color, info = self.color, self.info
        base = {'source_unix':stamp(color) if color else 0.,'ready':False,'people':[],
                'target_id':None,'distance_m':None,'bearing_rad':None,
                'selection_valid':False,'selected_indices':[],'selected_box':None}
        try:
            bgr = None
            if color is not None and -.1<=time.time()-stamp(color)<=1.:
                bgr=image_array(color)
            if color is None:
                raise ValueError('RGB 영상 수신 대기')
            if not -.1 <= time.time()-stamp(color) <= .25:
                raise ValueError('카메라 영상 지연')
            if stamp(color) == self.last_processed:
                return
            self.last_processed = stamp(color)
            start = time.monotonic()
            people = self.yolo.detect(bgr)
            base.update(people=people,width=color.width,height=color.height,
                        inference_ms=round((time.monotonic()-start)*1000,1))
            depths,qualities,errors=[],[],[]
            depth_array=None
            depth_problem='정렬 깊이·카메라 정보 수신 대기'
            if info is not None and self.depth:
                depth=min(self.depth,key=lambda d:abs(stamp(d)-stamp(color)))
                if (abs(stamp(depth)-stamp(color))>.06 or depth.width!=color.width or depth.height!=color.height
                        or depth.header.frame_id!=color.header.frame_id):
                    depth_problem='RGB와 정렬 깊이 동기화 대기'
                elif info.width!=color.width or info.height!=color.height or info.header.frame_id!=color.header.frame_id:
                    depth_problem='RGB 카메라 보정 정보 불일치'
                else:depth_array=image_array(depth)
            for person in people:
                quality={};distance=None;problem=depth_problem
                if depth_array is not None:
                    try:
                        distance,_=measure_depth(depth_array,person['box'],info.k[0],info.k[2],quality)
                        problem=''
                    except ValueError as exc:problem=str(exc)
                depths.append(distance);qualities.append(quality);errors.append(problem)
            signatures=[appearance_signature(bgr,p['box']) for p in people]
            target,stable=self.identity.update(people,time.monotonic(),signatures,depths)
            members=self.identity.selected_indices
            box=self.identity.selected_box
            base.update(target_id=target,selected_indices=members,selected_box=box,selection_valid=bool(members))
            if not members:
                raise ValueError('선택한 대상을 재확인하고 있습니다.' if people else '사람을 찾지 못했습니다.')
            if any(depths[i] is None for i in members):
                base['depth_quality']=qualities[members[0]]
                raise ValueError(next(errors[i] for i in members if depths[i] is None))
            # Split-leg association uses measured patches on each leg, never the gap between legs.
            quality={'regions':[region for i in members for region in qualities[i].get('regions',[])],
                     'valid_pixels':sum(qualities[i]['valid_pixels'] for i in members),
                     'pixels':sum(qualities[i]['pixels'] for i in members),
                     'selected_patches':sum(qualities[i]['selected_patches'] for i in members)}
            quality['valid_fraction']=round(quality['valid_pixels']/max(1,quality['pixels']),3)
            base.update(distance_m=min(depths[i] for i in members),
                        bearing_rad=-math.atan2(box[0]+box[2]/2-info.k[2],info.k[0]),depth_quality=quality)
            if time.time()-stamp(color) > .35:
                raise ValueError('검출 처리 지연')
            base.update(ready=stable,reason='' if stable else '동일한 사람 확인 중')
            self.emit(base)
        except (ValueError,KeyError,TypeError,cv2.error) as exc:
            # Missing depth does not mean a visually tracked person changed identity.
            # Missing RGB/people retains identity only within Identity.MAX_GAP.
            base.update(ready=False,reason=str(exc))
            self.emit(base)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--engine',required=True)
    args=parser.parse_args()
    rclpy.init()
    node=Detector(args.engine)
    try:rclpy.spin(node)
    except KeyboardInterrupt:pass
    finally:
        node.yolo.close();node.destroy_node()
        if rclpy.ok():rclpy.shutdown()


if __name__=='__main__':main()
