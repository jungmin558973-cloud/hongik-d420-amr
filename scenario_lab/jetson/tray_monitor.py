#!/usr/bin/env python3
"""Poll p only. Actuation occurs solely on explicit, fresh GUI requests."""
import argparse
import json
import subprocess
import time

from tray_ports import validate_port
from tray_protocol import TraySession


def main():
    import serial
    import rclpy
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
    last_publish=0.0

    def receive(msg):
        try: session.request(json.loads(msg.data))
        except (ValueError,KeyError,TypeError) as exc: session.error=str(exc)

    subscription=node.create_subscription(String,'/d420/tray/request',receive,10)

    def tick():
        nonlocal last_publish
        now=time.monotonic()
        try:
            status=session.tick()
        except (OSError,serial.SerialException,ValueError) as exc:
            session.error=str(exc);session.last_frame=0.0
            status=session.status()
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
