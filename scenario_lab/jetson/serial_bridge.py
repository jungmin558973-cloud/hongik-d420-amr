#!/usr/bin/env python3
"""D420 single serial owner: /odom + TF, optionally /d420/manual_cmd_vel (gated).
Optional bounded local route; motor output disabled by default. 20Hz control.
CAUTION: No safety-rated E-stop; disconnect power physically for emergencies.
"""
import argparse
import math
import sys
import time

from d420_core import BAUD, STOP, Decoder, motion_packet


def run(port, allow_motion, hz=20, max_linear_speed=0.30, max_angular_speed=0.30, local_route_factory=None):
    motion_packet(0., 0., max_linear_speed=max_linear_speed, max_angular_speed=max_angular_speed)
    import serial
    import rclpy
    from rclpy.node import Node
    from nav_msgs.msg import Odometry
    from geometry_msgs.msg import TransformStamped, Twist
    from tf2_ros import TransformBroadcaster

    class Bridge(Node):
        def __init__(self):
            super().__init__('d420_ros_bridge')
            self.ser = serial.Serial(port, BAUD, timeout=0, write_timeout=0.1)
            self.decoder = Decoder()
            self.x = self.y = 0.0
            self.last_step = time.monotonic()
            self.last_log = time.monotonic()
            self.last_cmd_time = 0.0
            self.cmd_v = self.cmd_w = 0.0
            self.motion_enabled = False
            self.motion_requested = bool(allow_motion)
            self.last_tx = 0.0
            self.tx_count = 0
            self.last_tx_state = None
            self.odom_pub = self.create_publisher(Odometry, '/odom', 10)
            self.transform_pub = TransformBroadcaster(self)
            self.create_subscription(Twist, '/d420/manual_cmd_vel', self.receive_cmd, 10)
            self.local_route = local_route_factory(self) if allow_motion and local_route_factory else None
            self.timer = self.create_timer(1.0 / hz, self.step)
            self.get_logger().info('Single serial owner: %s @ %s; motion=%s; topic=/d420/manual_cmd_vel' %
                                   (port, BAUD, 'ARMABLE' if allow_motion else 'DISABLED (read-only)'))

        def receive_cmd(self, msg):
            # Only allow 4WD differential x / z. No commands forwarded until 4WD ID + fresh telemetry.
            vx, wz = msg.linear.x, msg.angular.z
            if self.local_route and (vx != 0.0 or wz != 0.0):
                self.local_route.manual_takeover()
            if not (math.isfinite(vx) and math.isfinite(wz)):
                self.cmd_v = self.cmd_w = 0.0
                self.last_cmd_time = 0.0
                return
            self.cmd_v = max(-max_linear_speed, min(max_linear_speed, vx))
            self.cmd_w = max(-max_angular_speed, min(max_angular_speed, wz))
            self.last_cmd_time = time.monotonic()

        def step(self):
            now = time.monotonic()
            try:
                data = self.ser.read(min(self.ser.in_waiting or 1, 4096))
                if data:
                    self.decoder.feed(data, now)
                status_live = self.decoder.is_4wd and now - self.decoder.last_frame < 0.75
                v_live = self.decoder.v is not None and now - self.decoder.last_velocity < 0.5
                yaw_live = self.decoder.yaw is not None and now - self.decoder.last_yaw < 1.0
                live = status_live and v_live and yaw_live
                cmd_live = (now - self.last_cmd_time) < 0.25
                local = self.local_route.tick(now,live,self.x,self.y,self.decoder) if self.local_route else None
                out_v,out_w = local if local is not None else (self.cmd_v,self.cmd_w)
                if local is not None:cmd_live=True
                # Write a stop even with motion disabled, to guard against delayed command sources;
                # READ ONLY mode never writes anything at all.
                if self.motion_requested:
                    if live and cmd_live:
                        packet = motion_packet(out_v, out_w, max_linear_speed=max_linear_speed, max_angular_speed=max_angular_speed)
                    else:
                        packet = STOP
                    written = self.ser.write(packet)
                    if written != len(packet):
                        raise serial.SerialTimeoutException('Incomplete motor command: %s/%s bytes' % (written, len(packet)))
                    self.tx_count += 1
                    self.last_tx = now
                    tx_state = (packet, live, cmd_live)
                    if tx_state != self.last_tx_state:
                        self.get_logger().info(
                            'MOTOR TX bytes=%s packet=%s requested=(%+.3f,%+.3f) live=%s cmd_live=%s' %
                            (written, packet.hex(' '), out_v, out_w, live, cmd_live))
                        self.last_tx_state = tx_state
                if live:
                    elapsed = now - self.last_step
                    if 0.0 < elapsed < 0.15:
                        vx, vy, wz = self.decoder.v
                        yaw = self.decoder.yaw
                        self.x += (vx * math.cos(yaw) - vy * math.sin(yaw)) * elapsed
                        self.y += (vx * math.sin(yaw) + vy * math.cos(yaw)) * elapsed
                    self.publish_odom()
                if now - self.last_log >= 5:
                    self.get_logger().info('4WD=%s live=%s cmd_live=%s rx=%s bad=%s odom=(%.2f,%.2f) tx=%s cmd=(%+.3f,%+.3f) feedback=%s' %
                                           (self.decoder.is_4wd, live, cmd_live,
                                            self.decoder.good, self.decoder.bad, self.x, self.y,
                                            self.tx_count, self.cmd_v, self.cmd_w, self.decoder.v))
                    self.last_log = now
            except (OSError, serial.SerialException, serial.SerialTimeoutException) as exc:
                self.get_logger().error('SERIAL ERROR: %s — motor power must be stopped physically if needed' % exc)
                # Exit so launch detects a failed bridge instead of leaving a
                # discoverable ROS node whose serial timer has stopped forever.
                raise
            finally:
                self.last_step = now

        def publish_odom(self):
            stamp = self.get_clock().now().to_msg()
            yaw = self.decoder.yaw
            qz, qw = math.sin(yaw / 2), math.cos(yaw / 2)
            vx, vy, wz = self.decoder.v
            o = Odometry()
            o.header.stamp = stamp
            o.header.frame_id = 'odom'
            o.child_frame_id = 'base_footprint'
            o.pose.pose.position.x = self.x
            o.pose.pose.position.y = self.y
            o.pose.pose.orientation.z = qz
            o.pose.pose.orientation.w = qw
            o.twist.twist.linear.x = vx
            o.twist.twist.linear.y = vy
            o.twist.twist.angular.z = wz
            # Placeholder only. Validate position/yaw and tune noise before Nav2.
            o.pose.covariance[0] = o.pose.covariance[7] = 0.25
            o.pose.covariance[35] = 0.15
            o.twist.covariance[0] = o.twist.covariance[7] = o.twist.covariance[35] = 0.1
            self.odom_pub.publish(o)
            t = TransformStamped()
            t.header.stamp = stamp
            t.header.frame_id = 'odom'
            t.child_frame_id = 'base_footprint'
            t.transform.translation.x = self.x
            t.transform.translation.y = self.y
            t.transform.rotation.z = qz
            t.transform.rotation.w = qw
            self.transform_pub.sendTransform(t)

        def destroy_node(self):
            try:
                if self.ser.is_open:
                    if self.motion_requested:
                        for _ in range(10):
                            try:
                                self.ser.write(STOP)
                                time.sleep(.05)
                            except Exception:
                                break
                    self.ser.close()
            finally:
                super().destroy_node()

    rclpy.init()
    node = None
    try:
        node = Bridge()
        rclpy.spin(node)
    except (OSError, serial.SerialException) as exc:
        print('Cannot open serial port: %s' % exc, file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='D420 serial owner — odom and optional manual ROS commands')
    p.add_argument('--port', default='/dev/ttyUSB1')
    p.add_argument('--allow-motion', action='store_true', help='Send real motor commands. Test wheels up; see README.')
    p.add_argument('--max-linear-speed', type=float, default=0.30, help='Configured ceiling, up to D420 catalogue 1.50 m/s')
    p.add_argument('--max-angular-speed', type=float, default=0.30, help='Configured angular ceiling up to 1.0 rad/s')
    opts = p.parse_args()
    run(opts.port, opts.allow_motion, max_linear_speed=opts.max_linear_speed, max_angular_speed=opts.max_angular_speed)
