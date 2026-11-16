"""ROS adapter loaded only after the operator selects real-robot connection."""
import math
import threading
import time
import base64
import io

from engine import Pose


class RosRobot:
    def __init__(self):
        import rclpy
        from rclpy.context import Context
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import qos_profile_sensor_data
        from geometry_msgs.msg import Twist
        from nav_msgs.msg import Odometry
        from nav_msgs.msg import OccupancyGrid
        from sensor_msgs.msg import LaserScan, Image, CompressedImage
        from std_msgs.msg import String
        from tf2_ros import Buffer, TransformListener
        self.rclpy, self.Twist = rclpy, Twist
        self.lock = threading.RLock()
        self.context = Context()
        rclpy.init(context=self.context)
        self.node = rclpy.create_node('hongik_scenario_lab', context=self.context)
        self.pose = Pose()
        self.points = []
        self.odom_at = self.scan_at = 0.0
        self.odom_stamp = self.scan_stamp = 0.0
        self.scan_valid = False
        self.rear_beams = 0
        self.follow_coverage = {'front':[],'rear':[]}
        self.tf_error = '라이다 TF 대기 중'
        self.last_stamp = None
        self.discontinuity = False
        self.tray_data = {'connected':False,'error':'ESP32 상태 대기'}
        self.tray_at = 0.0
        self.follow_data = {'ready':False,'reason':'YOLO 검출기를 준비해 주세요.'}
        self.follow_at = 0.0
        self.depth_data={'version':1,'ready':False,'reason':'깊이 관측 연결 대기'}
        self.depth_at=0.
        self.local_route_data={};self.local_route_at=0.;self.local_stop=None
        self.String = String
        self.camera_jpeg = None
        self.camera_at = self.camera_stamp = self.compressed_at = 0.0
        self.camera_seq = 0
        self.camera_size = (0, 0)
        self.camera_error = 'RealSense RGB 수신 대기'
        self.pending_camera = self.pending_map = None
        self.map_image = None
        self.map_metadata = None
        self.map_grid = None
        self.nav = None
        self.map_seq = 0
        self.worker_event = threading.Event()
        self.worker_stop = threading.Event()
        self.last_raw_queued = 0.0
        self.Image = Image
        self.raw_sub = None
        self.buffer = Buffer()
        self.listener = TransformListener(self.buffer, self.node)
        self.odom_sub = self.node.create_subscription(Odometry, '/odom', self.odom, qos_profile_sensor_data)
        self.scan_sub = self.node.create_subscription(LaserScan, '/scan', self.scan, qos_profile_sensor_data)
        camera_qos = rclpy.qos.QoSProfile(depth=1, reliability=rclpy.qos.ReliabilityPolicy.BEST_EFFORT)
        self.compressed_sub = self.node.create_subscription(CompressedImage,
            '/camera/camera/color/image_raw/compressed', self.compressed_image, camera_qos)
        self.follow_camera_sub = self.node.create_subscription(CompressedImage,
            '/d420/follow/camera/compressed', self.compressed_image, camera_qos)
        self.camera_transport_timer = self.node.create_timer(2.0, self.choose_camera_transport)
        map_qos = rclpy.qos.QoSProfile(depth=1, reliability=rclpy.qos.ReliabilityPolicy.RELIABLE,
                                      durability=rclpy.qos.DurabilityPolicy.TRANSIENT_LOCAL)
        self.map_sub = self.node.create_subscription(OccupancyGrid, '/map', self.occupancy_map, map_qos)
        self.publisher = self.node.create_publisher(Twist, '/d420/manual_cmd_vel', 10)
        self.tray_publisher = self.node.create_publisher(String, '/d420/tray/request', 10)
        self.tray_sub = self.node.create_subscription(String, '/d420/tray/status', self.tray_status, 10)
        self.follow_sub = self.node.create_subscription(String, '/d420/follow/observation', self.follow_status, 10)
        self.local_route_pub=self.node.create_publisher(String,'/d420/local_route/request',10)
        self.local_route_sub=self.node.create_subscription(String,'/d420/local_route/status',self.local_route_status,10)
        self.local_stop_timer=self.node.create_timer(.1,self.retry_local_stop)
        self.depth_sub = self.node.create_subscription(String, '/d420/depth_route/observation', self.depth_status, camera_qos)
        self.executor = SingleThreadedExecutor(context=self.context)
        self.executor.add_node(self.node)
        self.thread = threading.Thread(target=self.executor.spin, daemon=True)
        self.thread.start()
        self.media_thread = threading.Thread(target=self.encode_media, daemon=True)
        self.media_thread.start()

    def local_route_status(self,msg):
        import json
        try:
            if len(msg.data)>300000:return
            data=json.loads(msg.data)
            if not isinstance(data,dict) or data.get('version')!=1:return
            with self.lock:
                self.local_route_data,self.local_route_at=data,time.monotonic()
                if self.local_stop:
                    reply=data.get('reply',{})
                    if reply.get('request_id')==self.local_stop['request_id'] and reply.get('accepted'):
                        self.local_stop=None
        except (ValueError,TypeError):pass

    def local_route(self):
        with self.lock:data=dict(self.local_route_data);at=self.local_route_at
        data['transport_age_ms']=(time.monotonic()-at)*1000 if at else 999999
        if self.node.count_publishers('/d420/local_route/status')!=1:
            data['transport_age_ms']=999999
        return data

    def send_local_route(self,data):
        import json
        msg=self.String();msg.data=json.dumps(data,allow_nan=False)
        if data.get('op')=='stop':
            with self.lock:self.local_stop=dict(data)
        self.local_route_pub.publish(msg)

    def retry_local_stop(self):
        with self.lock:data=self.local_stop
        if data:self.send_local_route(data)

    def depth_status(self,msg):
        import json
        try:
            if len(msg.data)>200000:return
            data=json.loads(msg.data)
            if not isinstance(data,dict) or data.get('version')!=1:return
            wall,mono=time.time(),time.monotonic()
            with self.lock:
                data['receive_gap_ms']=(mono-self.depth_at)*1000 if self.depth_at else None
                for key in ('source_unix','published_unix'):
                    value=data.get(key)
                    if type(value) in (int,float) and math.isfinite(value):
                        data['receive_'+('source' if key=='source_unix' else 'publish')+'_age_ms']=(wall-value)*1000
                self.depth_data,self.depth_at=data,mono
        except (ValueError,TypeError):pass

    def depth(self):
        with self.lock:data=dict(self.depth_data);at=self.depth_at
        data['transport_age_ms']=(time.monotonic()-at)*1000 if at else None
        count=self.node.count_publishers('/d420/depth_route/observation')
        if count>1:
            data.update(ready=False,fault='duplicate_publishers',reason='깊이 관측 발행자가 중복됐습니다.')
        elif count!=1 or time.monotonic()-at>.35:
            data.update(ready=False,fault='transport_stale',reason='깊이 관측 연결/지연 확인 필요')
        return data

    def follow_status(self, msg):
        import json
        try:
            if len(msg.data)>16384:return
            data=json.loads(msg.data)
            if not isinstance(data,dict) or data.get('version')!=1:return
            with self.lock:self.follow_data,self.follow_at=data,time.monotonic()
        except (ValueError,TypeError):pass

    def follow(self):
        with self.lock:data,at=dict(self.follow_data),self.follow_at
        count=self.node.count_publishers('/d420/follow/observation')
        if count>1:
            data.update(ready=False,fault='duplicate_publishers',reason='YOLO 검출 발행자가 중복됐습니다.')
        elif time.monotonic()-at>.4 or count!=1:
            data.update(ready=False,fault='transport_stale',reason='YOLO 검출 수신이 지연됐습니다.')
        return data

    def tray_status(self, msg):
        import json
        try:
            data=json.loads(msg.data)
            if not isinstance(data,dict) or not isinstance(data.get('connected'),bool): return
            if data.get('imu_fresh'):
                if not all(isinstance(data.get(k),(int,float)) and math.isfinite(data[k]) and abs(data[k])<=360 for k in ['roll_deg','pitch_deg']): return
            with self.lock:
                self.tray_data=data;self.tray_at=time.monotonic()
        except (ValueError,TypeError): pass

    def tray(self):
        with self.lock:
            data=dict(self.tray_data)
            age=time.monotonic()-self.tray_at
        source=data.get('stamp_unix',0)
        if age>1.0 or not isinstance(source,(int,float)) or not -.15<=time.time()-source<=1.0:
            data.update(connected=False,error='ESP32 상태 수신 대기/지연',roll_deg=None,pitch_deg=None)
        elif self.node.count_publishers('/d420/tray/status')!=1:
            data.update(connected=False,error='ESP32 상태 발행자는 하나여야 합니다.')
        data['transport_age_s']=age if self.tray_at else None
        return data

    def send_tray(self, command):
        import json,uuid
        from jetson.tray_protocol import check_command
        check_command(command,self.tray())
        if self.node.count_subscribers('/d420/tray/request')!=1:
            raise ValueError('ESP32 명령 수신자는 하나여야 합니다.')
        nonce=uuid.uuid4().hex
        msg=self.String();msg.data=json.dumps({'command':command,'issued_at':time.time(),'nonce':nonce})
        self.tray_publisher.publish(msg)
        return nonce

    def choose_camera_transport(self):
        # A raw fallback across WiFi starved aligned depth and exhausted D435 frames.
        # The local camera relay runs with the sensor stack, including while YOLO restarts.
        if self.raw_sub is not None:
            self.node.destroy_subscription(self.raw_sub)
            self.raw_sub=None

    def compressed_image(self, msg):
        data = bytes(msg.data)
        if not data.startswith(b'\xff\xd8'):
            return
        with self.lock:
            self.compressed_at = time.monotonic()
            self.pending_camera = ('jpeg', data, self.stamp(msg), self.compressed_at)
        self.worker_event.set()

    def raw_image(self, msg):
        now = time.monotonic()
        if now-self.compressed_at < 3.0 or now-self.last_raw_queued < 0.15:
            return
        self.last_raw_queued = now
        frame = (msg.width, msg.height, msg.encoding, msg.step, bytes(msg.data))
        with self.lock:
            self.pending_camera = ('raw', frame, self.stamp(msg), now)
        self.worker_event.set()

    def occupancy_map(self, msg):
        origin = msg.info.origin.position
        if (msg.info.width*msg.info.height > 4_000_000 or
            not math.isfinite(msg.info.resolution) or msg.info.resolution <= 0 or
            not all(math.isfinite(v) for v in [origin.x, origin.y])):
            return
        with self.lock:
            self.pending_map = msg
        self.worker_event.set()

    def encode_media(self):
        from PIL import Image
        from media import encode_camera, encode_grid
        while not self.worker_stop.is_set():
            self.worker_event.wait(0.25)
            self.worker_event.clear()
            with self.lock:
                camera, grid = self.pending_camera, self.pending_map
                self.pending_camera = self.pending_map = None
            if camera:
                try:
                    kind, frame, stamp, received = camera
                    jpeg = frame if kind == 'jpeg' else encode_camera(*frame)
                    with Image.open(io.BytesIO(jpeg)) as image:
                        size = image.size
                    with self.lock:
                        self.camera_jpeg, self.camera_size = jpeg, size
                        self.camera_stamp, self.camera_at = stamp, received
                        self.camera_seq += 1
                        self.camera_error = ''
                except Exception as exc:
                    with self.lock:
                        self.camera_error = f'카메라 디코딩 오류: {exc}'
            if grid:
                try:
                    png, _, _, _ = encode_grid(grid.info.width, grid.info.height, grid.data)
                    q = grid.info.origin.orientation
                    yaw = math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))
                    metadata = {'frame':grid.header.frame_id,
                                'x':grid.info.origin.position.x,'y':grid.info.origin.position.y,
                                'yaw':yaw,'width':grid.info.width*grid.info.resolution,
                                'height':grid.info.height*grid.info.resolution,
                                'resolution':grid.info.resolution,'received':time.monotonic()}
                    with self.lock:
                        self.map_image = 'data:image/png;base64,'+base64.b64encode(png).decode()
                        self.map_metadata = metadata
                        self.map_grid = grid
                        self.map_seq += 1
                except Exception:
                    pass

    def media(self):
        from rclpy.time import Time
        with self.lock:
            now = time.monotonic()
            camera = {'available': self.camera_jpeg is not None and now-self.camera_at<2.0,
                      'age': now-self.camera_at if self.camera_at else None,
                      'seq': self.camera_seq, 'width':self.camera_size[0], 'height':self.camera_size[1],
                      'error':self.camera_error}
            metadata = dict(self.map_metadata) if self.map_metadata else None
            png, seq = self.map_image, self.map_seq
        if metadata and now-metadata['received']<8.0:
            try:
                transform = self.buffer.lookup_transform('odom',metadata['frame'],Time()).transform
                q = transform.rotation
                yaw = math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))
                c,s = math.cos(yaw),math.sin(yaw)
                x,y = metadata['x'],metadata['y']
                metadata.update({'x':transform.translation.x+c*x-s*y,
                                 'y':transform.translation.y+s*x+c*y,'yaw':yaw+metadata['yaw'],
                                 'image':png,'seq':seq})
            except Exception:
                metadata = None
        else:
            metadata = None
        return camera,metadata

    def camera_frame(self):
        with self.lock:
            if self.camera_jpeg is None or time.monotonic()-self.camera_at>2.0:
                return None
            return self.camera_jpeg

    def navigation(self):
        if self.nav is None:
            from ros_navigation import RosNavigation
            self.nav=RosNavigation(self.node)
        return self.nav

    def mapping(self):
        from rclpy.time import Time
        with self.lock:
            meta=dict(self.map_metadata) if self.map_metadata else None
            image,seq=self.map_image,self.map_seq
        if meta is None or time.monotonic()-meta['received']>8:
            raise ValueError('최신 라이다 지도를 기다리고 있습니다.')
        publishers=self.node.get_publishers_info_by_topic('/map')
        if len(publishers)!=1:raise ValueError('지도 발행자는 하나여야 합니다.')
        session=bytes(publishers[0].endpoint_gid).hex()
        try:transform=self.buffer.lookup_transform('map','base_footprint',Time())
        except Exception as exc:raise ValueError('지도상의 로봇 위치를 기다리고 있습니다.') from exc
        stamp=self.stamp(transform)
        if not -.15<=self.node.get_clock().now().nanoseconds/1e9-stamp<=.5:
            raise ValueError('지도상의 로봇 위치가 지연됐습니다.')
        q=transform.transform.rotation;p=transform.transform.translation
        yaw=math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))
        return {**meta,'image':image,'seq':seq,'session':session,'robot':{'x':p.x,'y':p.y,'yaw':yaw}}

    def check_map_point(self,point):
        from navigation import check_map_point
        self.mapping()
        with self.lock:grid=self.map_grid
        return check_map_point(grid,point)

    @staticmethod
    def stamp(msg):
        return msg.header.stamp.sec + msg.header.stamp.nanosec / 1e9

    def odom(self, msg):
        q, p, v = msg.pose.pose.orientation, msg.pose.pose.position, msg.twist.twist
        yaw = math.atan2(2 * (q.w*q.z + q.x*q.y), 1 - 2*(q.y*q.y + q.z*q.z))
        values = [p.x, p.y, yaw, v.linear.x, v.angular.z]
        norm = q.x*q.x + q.y*q.y + q.z*q.z + q.w*q.w
        if not all(math.isfinite(x) for x in values) or not 0.95 < norm < 1.05:
            return
        with self.lock:
            stamp = self.stamp(msg)
            if self.last_stamp is not None and stamp < self.last_stamp - 0.05:
                self.discontinuity = True
            self.last_stamp = stamp
            self.pose = Pose(*values)
            self.odom_at, self.odom_stamp = time.monotonic(), stamp

    def scan(self, msg):
        from rclpy.time import Time
        from follow_clearance import coverage_bin
        try:
            tf = self.buffer.lookup_transform('base_footprint', msg.header.frame_id, Time()).transform
            q = tf.rotation
            if not all(math.isfinite(v) for v in [tf.translation.x, tf.translation.y, q.x, q.y, q.z, q.w]):
                raise ValueError('Invalid sensor transform')
            yaw = math.atan2(2*(q.w*q.z+q.x*q.y), 1-2*(q.y*q.y+q.z*q.z))
            c, s = math.cos(yaw), math.sin(yaw)
            points, valid, front, sides, rear = [], 0, 0, 0, 0
            coverage={key:[[0,0] for _ in range(3)] for key in ('front','rear')}
            for i, distance in enumerate(msg.ranges):
                angle = msg.angle_min + i * msg.angle_increment
                # +inf is a valid no-return beam; NaN/zero/too-close are not clear space.
                usable = (math.isinf(distance) and distance > 0) or (
                    math.isfinite(distance) and msg.range_min <= distance <= msg.range_max)
                bearing = math.atan2(math.sin(angle+yaw), math.cos(angle+yaw))
                for key in coverage:
                    index=coverage_bin(bearing,key=='rear')
                    if index is not None:
                        coverage[key][index][0]+=1;coverage[key][index][1]+=int(usable)
                if not usable:
                    continue
                valid += 1
                bearing = math.atan2(math.sin(angle+yaw), math.cos(angle+yaw))
                front += abs(bearing) < 0.65
                sides += 0.65 <= abs(bearing) <= 2.0
                rear += abs(bearing) > math.pi-.65
                if math.isfinite(distance):
                    x, y = distance * math.cos(angle), distance * math.sin(angle)
                    points.append((tf.translation.x+c*x-s*y, tf.translation.y+s*x+c*y))
            with self.lock:
                self.points = points
                self.scan_valid = len(msg.ranges) >= 100 and valid / len(msg.ranges) > 0.55 and front > 20 and sides > 30
                self.rear_beams = rear
                self.follow_coverage = coverage
                self.scan_at, self.scan_stamp = time.monotonic(), self.stamp(msg)
                self.tf_error = ''
        except Exception:
            with self.lock:
                self.tf_error = '라이다와 차체의 TF 연결을 확인해 주세요.'

    def read(self,require_lidar=True):
        with self.lock:
            pose = Pose(self.pose.x, self.pose.y, self.pose.yaw, self.pose.v, self.pose.w)
            points = list(self.points)
            now = time.monotonic()
            ros_now = self.node.get_clock().now().nanoseconds / 1e9
            reason = ''
            if self.discontinuity:
                reason = '오도메트리 시간이 초기화됐습니다. 연결을 다시 준비해 주세요.'
            elif now - self.odom_at > 0.35 or not -0.15 <= ros_now - self.odom_stamp <= 0.50:
                reason = '최신 오도메트리가 없습니다.'
            elif require_lidar and (now - self.scan_at > 0.55 or not -0.15 <= ros_now - self.scan_stamp <= 0.75):
                reason = '최신 라이다 스캔이 없습니다.'
            elif require_lidar and self.tf_error:
                reason = self.tf_error
            elif require_lidar and not self.scan_valid:
                reason = '라이다의 전방·측면 관측이 충분하지 않습니다.'
            elif self.node.count_publishers('/odom') != 1:
                reason = '오도메트리 발행자는 하나여야 합니다.'
            elif self.node.count_publishers('/d420/manual_cmd_vel') != 1:
                reason = '다른 WASD/자율주행 명령 발행자를 종료해 주세요.'
            elif self.node.count_subscribers('/d420/manual_cmd_vel') != 1:
                reason = '차체 브리지 수신자는 하나여야 합니다.'
            elif require_lidar and (now-self.camera_at>2.0 or not -0.15 <= ros_now-self.camera_stamp <= 2.0):
                reason = '최신 RealSense 영상 수신을 기다리고 있습니다.'
            return pose, points, reason

    def motion_reason(self, v, w):
        with self.lock:
            if (v < -.001 or abs(w)>.01) and self.rear_beams<=20:
                return '후방 라이다 관측이 부족해 후진/회전을 중지합니다.'
        return ''

    def follow_motion_reason(self,v,w):
        from follow_clearance import motion_reason
        with self.lock:
            ros_now=self.node.get_clock().now().nanoseconds/1e9
            fresh=(time.monotonic()-self.scan_at<.35 and -.15<=ros_now-self.scan_stamp<=.5 and not self.tf_error)
            # Raw encoder sign is uncalibrated; retain commanded direction but use
            # the larger measured speed when calculating clearance while braking.
            check_v=math.copysign(max(abs(v),abs(self.pose.v)),v) if abs(v)>1e-6 else v
            check_w=math.copysign(max(abs(w),abs(self.pose.w)),w) if abs(w)>1e-6 else w
            return motion_reason(self.points,check_v,check_w,self.follow_coverage,fresh)

    def publish(self, v, w):
        msg = self.Twist()
        msg.linear.x, msg.angular.z = float(v), float(w)
        self.publisher.publish(msg)

    def close(self):
        if self.nav is not None:self.nav.cancel()
        for _ in range(8):
            self.publish(0, 0)
            time.sleep(0.025)
        self.worker_stop.set()
        self.worker_event.set()
        self.media_thread.join(timeout=2)
        self.executor.shutdown(timeout_sec=2)
        self.thread.join(timeout=2)
        self.node.destroy_node()
        self.rclpy.shutdown(context=self.context)
