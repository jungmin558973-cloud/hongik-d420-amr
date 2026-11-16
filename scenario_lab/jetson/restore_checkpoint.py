#!/usr/bin/env python3
"""Restore the stationary deployment checkpoint; never publish motor commands."""
import argparse
import json
import math
from pathlib import Path
import time

import rclpy
from nav_msgs.msg import OccupancyGrid,Odometry
from rclpy.qos import QoSProfile,ReliabilityPolicy,DurabilityPolicy,qos_profile_sensor_data
from rclpy.time import Time
from slam_toolbox.srv import DeserializePoseGraph
from tf2_ros import Buffer,TransformListener


def main():
    parser=argparse.ArgumentParser();parser.add_argument('checkpoint');args=parser.parse_args()
    checkpoint=json.loads(Path(args.checkpoint).read_text());pose=checkpoint['pose'];grid=checkpoint['grid']
    path=Path(checkpoint['map']['remote_path'])
    if path.parent!=Path.home()/'d420_maps' or any(not path.with_suffix(s).is_file() for s in ('.yaml','.pgm','.data','.posegraph')):
        raise RuntimeError('Verified checkpoint map files required')
    if any(type(pose.get(k)) not in (int,float) or not math.isfinite(pose[k]) for k in ('x','y','yaw')):
        raise RuntimeError('Finite checkpoint map pose required')
    rclpy.init();node=rclpy.create_node('hongik_restore_speed_checkpoint')
    buffer=Buffer();listener=TransformListener(buffer,node);maps=[];odom=[]
    node.create_subscription(OccupancyGrid,'/map',lambda m:maps.append((time.monotonic(),m)),
        QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL))
    node.create_subscription(Odometry,'/odom',lambda m:odom.append((time.monotonic(),m.twist.twist)),qos_profile_sensor_data)
    try:
        end=time.monotonic()+3
        while time.monotonic()<end:rclpy.spin_once(node,timeout_sec=.1)
        recent=[v for t,v in odom if time.monotonic()-t<1]
        if node.count_publishers('/d420/manual_cmd_vel') or len(recent)<3 or any(abs(v.linear.x)>.01 or abs(v.angular.z)>.03 for v in recent):
            raise RuntimeError('Close motor publishers and keep chassis stationary during restore')
        client=node.create_client(DeserializePoseGraph,'/slam_toolbox/deserialize_map')
        if not client.wait_for_service(timeout_sec=10):raise RuntimeError('SLAM restore service unavailable')
        request=DeserializePoseGraph.Request();request.filename=str(path)
        request.match_type=DeserializePoseGraph.Request.START_AT_GIVEN_POSE
        request.initial_pose.x=pose['x'];request.initial_pose.y=pose['y'];request.initial_pose.theta=pose['yaw']
        future=client.call_async(request);rclpy.spin_until_future_complete(node,future,timeout_sec=30)
        if not future.done() or future.exception():raise RuntimeError('SLAM restore call failed')
        called=time.monotonic();end=called+20
        while time.monotonic()<end:
            rclpy.spin_once(node,timeout_sec=.1)
            if not maps or maps[-1][0]<=called:continue
            m=maps[-1][1];info=m.info
            if (abs(info.resolution-grid['resolution'])>1e-6 or
                info.width*info.resolution<grid['width']-.25 or info.height*info.resolution<grid['height']-.25 or
                abs(info.origin.position.x-grid['x'])>.25 or abs(info.origin.position.y-grid['y'])>.25):continue
            try:t=buffer.lookup_transform('map','base_footprint',Time())
            except Exception:continue
            q=t.transform.rotation;p=t.transform.translation
            yaw=math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))
            angle=(yaw-pose['yaw']+math.pi)%(2*math.pi)-math.pi
            if math.hypot(p.x-pose['x'],p.y-pose['y'])>.2 or abs(angle)>.2:continue
            publishers=node.get_publishers_info_by_topic('/map')
            if len(publishers)!=1:raise RuntimeError('Exactly one map publisher required')
            print(json.dumps({'restored':str(path),'session':bytes(publishers[0].endpoint_gid).hex(),
                'robot':{'x':p.x,'y':p.y,'yaw':yaw},'width':info.width,'height':info.height}))
            return
        raise RuntimeError('Could not verify restored map bounds and robot pose; keep robot stopped')
    finally:node.destroy_node();rclpy.shutdown()


if __name__=='__main__':main()
