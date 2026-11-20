#!/usr/bin/env python3
"""Local raw RGB → latest JPEG at 10Hz, independent of person detection. No motors."""
import time
import cv2
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image, CompressedImage
from camera_frame import image_array, stamp


class CameraRelay(Node):
    def __init__(self):
        super().__init__('hongik_camera_relay')
        self.frame=None;self.last=None
        latest=QoSProfile(depth=1,reliability=ReliabilityPolicy.BEST_EFFORT)
        self.pub=self.create_publisher(CompressedImage,'/d420/follow/camera/compressed',latest)
        self.create_subscription(Image,'/camera/camera/color/image_raw',self.receive,latest)
        self.create_timer(.1,self.publish_latest)

    def receive(self,msg):self.frame=msg

    def publish_latest(self):
        frame=self.frame
        if frame is None or stamp(frame)==self.last or not -.1<=time.time()-stamp(frame)<=.35:return
        self.last=stamp(frame)
        try:
            ok,data=cv2.imencode('.jpg',image_array(frame),[cv2.IMWRITE_JPEG_QUALITY,70])
            if not ok:return
            jpeg=CompressedImage();jpeg.header=frame.header;jpeg.format='jpeg';jpeg.data=data.tobytes()
            self.pub.publish(jpeg)
        except (ValueError,KeyError,TypeError,cv2.error) as exc:
            self.get_logger().warning('JPEG frame rejected: '+str(exc),throttle_duration_sec=5.)


def main():
    rclpy.init();node=CameraRelay()
    try:rclpy.spin(node)
    except KeyboardInterrupt:pass
    finally:
        node.destroy_node()
        if rclpy.ok():rclpy.shutdown()


if __name__=='__main__':main()
