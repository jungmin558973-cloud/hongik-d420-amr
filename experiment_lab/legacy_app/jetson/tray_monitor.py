#!/usr/bin/env python3
"""Poll p only. Actuation occurs solely on explicit, fresh GUI requests."""
import argparse
import json
import subprocess
import time

from tray_ports import validate_port
from tray_protocol import TraySession,is_prediction
from route_tray_link import RouteTrayReceiver


def main():
    import serial
    import rclpy
    from rclpy.qos import QoSProfile,ReliabilityPolicy
    from std_msgs.msg import String
    parser=argparse.ArgumentParser();parser.add_argument('--port',required=True)
    args=parser.parse_args()
    identity=validate_port(args.port)
    owner=subprocess.run(['fuser',identity['device']],capture_output=True,text=True)
    if owner.returncode!=1:
        raise RuntimeError('ESP32 포트가 사용 중입니다. Arduino Serial Monitor 등 다른 프로그램을 닫아 주세요.')
    port=serial.Serial(port=None,baudrate=115200,timeout=0,write_timeout=.1,exclusive=True)
    port.dtr=False;port.rts=False;port.port=args.port;port.open()
    rclpy.init();node=rclpy.create_node('hongik_tray_monitor')
    publisher=node.create_publisher(String,'/d420/tray/status',10)
    session=TraySession(port,identity)
    route=RouteTrayReceiver(session)
    last_publish=0.0

    def receive(msg):
        try:
            request=json.loads(msg.data)
            # Explicit operator commands win over an automated route lease.
            if route.lease and is_prediction(request.get('command')):raise ValueError('Jetson 경로가 예측 목표를 전송 중입니다.')
            session.request(request)
            if route.lease:route.revoke('운영자 명령으로 경로 목표 해제',time.monotonic(),time.time())
        except (ValueError,KeyError,TypeError) as exc: session.error=str(exc)

    subscription=node.create_subscription(String,'/d420/tray/request',receive,10)
    def route_receive(msg):
        try:
            if len(msg.data)>4096:raise ValueError('경로 트레이 메시지 크기 초과')
            if node.count_publishers('/d420/tray/route_target')!=1:raise ValueError('경로 트레이 발행자 중복/미확인')
            route.receive(json.loads(msg.data))
        except (ValueError,KeyError,TypeError) as exc:session.error=str(exc)
    route_subscription=node.create_subscription(String,'/d420/tray/route_target',route_receive,QoSProfile(depth=1,reliability=ReliabilityPolicy.BEST_EFFORT))

    def tick():
        nonlocal last_publish
        now=time.monotonic()
        try:
            if route.lease and node.count_publishers('/d420/tray/route_target')!=1:
                route.revoke('경로 트레이 발행자 단절/중복',now,time.time(),level=True)
            route.tick(now)
            status=session.tick()
        except (OSError,serial.SerialException,ValueError) as exc:
            session.error=str(exc);session.last_frame=0.0
            status=session.status()
        status['route_control']=route.status(now)
        if now-last_publish>=.2:
            message=String();message.data=json.dumps(status,ensure_ascii=False,allow_nan=False)
            publisher.publish(message);last_publish=now

    timer=node.create_timer(.05,tick)
    try: rclpy.spin(node)
    except KeyboardInterrupt: pass
    finally:
        # Do not detach servos or silently change their mode when closing telemetry.
        timer.cancel();port.close();node.destroy_node()
        if rclpy.ok(): rclpy.shutdown()


if __name__=='__main__': main()
