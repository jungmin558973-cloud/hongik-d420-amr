"""Dynamic ROS discovery, raw CDR rosbag2 plus receive/source time JSONL.

ROS/rosbag imports are deferred so Windows mock mode has no ROS dependency.
"""
import json
import queue
import threading
import time
from .storage import dump

# Existing command owners enforce exactly one subscriber on these channels.
# Passive subscribers count too; record owner-side mirrors instead when available.
EXCLUSIVE_COMMAND_TOPICS={'/d420/manual_cmd_vel','/d420/tray/request','/d420/tray/route_target','/d420/local_route/request'}
BULK_TYPES={'sensor_msgs/msg/Image','sensor_msgs/msg/CompressedImage','sensor_msgs/msg/PointCloud2','nav_msgs/msg/OccupancyGrid'}


def json_safe(value):
    import math
    if isinstance(value,float) and not math.isfinite(value):return None
    if isinstance(value,dict):return {k:json_safe(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [json_safe(v) for v in value]
    return value


class RawRecorder:
    def __init__(self,path,topics):
        self.path=path;self.topics=topics;self.topic_names={t for t,k in topics};self.queue=queue.Queue(256);self.error='';self.ready=threading.Event()
        self.byte_lock=threading.Lock();self.queued_bytes=0
        self.dropped=0;self.closed=False
        self.thread=threading.Thread(target=self.write,daemon=True);self.thread.start()
        if not self.ready.wait(5) or self.error:raise ValueError('rosbag2 writer failed: '+self.error)

    def append(self,topic,typ,raw,stamp,row):
        if self.closed or topic not in self.topic_names:return
        with self.byte_lock:
            if self.queued_bytes+len(raw)>64*1024**2:
                self.dropped+=1;self.error='Raw log byte budget exceeded; abort experiment';return
            self.queued_bytes+=len(raw)
        try:self.queue.put_nowait((topic,typ,raw,stamp,row))
        except queue.Full:
            with self.byte_lock:self.queued_bytes-=len(raw)
            self.dropped+=1;self.error='Raw log queue overflow; abort experiment'

    def write(self):
        try:
            import rosbag2_py
            writer=rosbag2_py.SequentialWriter()
            writer.open(rosbag2_py.StorageOptions(uri=str(self.path/'rosbag2'),storage_id='sqlite3'),rosbag2_py.ConverterOptions('cdr','cdr'))
            for topic,typ in self.topics:
                writer.create_topic(rosbag2_py.TopicMetadata(name=topic,type=typ,serialization_format='cdr'))
            dump(self.path/'topic_inventory.json',self.topics)
            self.ready.set()
            with (self.path/'robot_raw.jsonl').open('a',encoding='utf-8') as rawlog,(self.path/'robot_telemetry.jsonl').open('a',encoding='utf-8') as telemetry,(self.path/'events.jsonl').open('a',encoding='utf-8') as events:
                seen_events=set()
                while True:
                    item=self.queue.get()
                    if item is None:break
                    topic,typ,raw,stamp,row=item;writer.write(topic,raw,stamp)
                    with self.byte_lock:self.queued_bytes-=len(raw)
                    rawlog.write(json.dumps(row,allow_nan=False)+'\n')
                    if topic=='/odom':telemetry.write(json.dumps(row['telemetry'],allow_nan=False)+'\n')
                    if topic=='/d420/experiment/status':
                        state=row.get('data',{})
                        for e in state.get('events',[]):
                            if e.get('run_id')!=self.path.name:continue
                            key=(e.get('mono'),e.get('event'),e.get('segment'))
                            if key in seen_events:continue
                            seen_events.add(key);event={**e,'utc':state['utc']+(e['mono']-state['mono']),'source':'jetson'}
                            events.write(json.dumps(event,allow_nan=False)+'\n')
                for f in (rawlog,telemetry,events):
                    import os
                    f.flush();os.fsync(f.fileno())
            del writer # Humble writer closes bag metadata on destruction.
        except Exception as e:self.error=type(e).__name__+': '+str(e);self.ready.set()

    def close(self):
        self.closed=True
        try:self.queue.put(None,timeout=2)
        except queue.Full:raise ValueError('Recorder queue jammed')
        self.thread.join(10)
        if self.thread.is_alive():raise ValueError('rosbag close timeout')
        if self.error:raise ValueError(self.error)


class RosCollector:
    def __init__(self,root):
        import rclpy
        from rclpy.qos import qos_profile_sensor_data
        from std_msgs.msg import String
        import uuid
        self.rclpy=rclpy;rclpy.init();self.node=rclpy.create_node('hongik_experiment_agent_'+uuid.uuid4().hex[:8])
        self.String=String;self.qos=qos_profile_sensor_data;self.root=root;self.recorder=None
        self.subs={};self.inventory=[];self.latest={};self.status={};self.error='';self.seq=0
        self.record_lock=threading.RLock()
        self.publisher=self.node.create_publisher(String,'/d420/experiment/request',10)
        self.node.create_timer(1.,self.discover)
        self.thread=threading.Thread(target=rclpy.spin,args=(self.node,),daemon=True);self.thread.start()

    def discover(self):
        from rosidl_runtime_py.utilities import get_message
        from rclpy.qos import QoSProfile,ReliabilityPolicy,DurabilityPolicy
        self.inventory=self.node.get_topic_names_and_types()
        for topic,types in self.inventory:
            if topic in EXCLUSIVE_COMMAND_TOPICS:continue
            if topic in self.subs or not types:continue
            # C920 is the experiment video. D435 images/clouds/maps are optional
            # bulk streams and must not starve odom/IMU or fill trial storage.
            if types[0] in BULK_TYPES:continue
            try:
                typ=types[0];cls=get_message(typ);offered=self.node.get_publishers_info_by_topic(topic)
                qos=QoSProfile(depth=5,reliability=ReliabilityPolicy.BEST_EFFORT,durability=DurabilityPolicy.VOLATILE)
                self.subs[topic]=self.node.create_subscription(cls,topic,lambda raw,t=topic,k=typ,c=cls:self.receive(t,k,c,raw),qos,raw=True)
            except Exception as e:self.error='Discovery/subscription failed: '+topic+' '+type(e).__name__

    def receive(self,topic,typ,cls,raw):
        from rclpy.serialization import deserialize_message
        from rosidl_runtime_py.convert import message_to_ordereddict
        mono=time.monotonic();utc=time.time();self.seq+=1
        row={'topic':topic,'type':typ,'utc':utc,'mono':mono,'seq':self.seq,'ros_stamp':None,'bytes':len(raw)}
        try:
            msg=deserialize_message(raw,cls)
            if hasattr(msg,'header'):
                s=msg.header.stamp;row['ros_stamp']=s.sec+s.nanosec/1e9
            if topic=='/d420/experiment/status':self.status=json.loads(msg.data);row['data']=self.status
            elif topic=='/d420/experiment/motor_output':self.latest['motor']=json.loads(msg.data);row['data']=self.latest['motor']
            elif topic=='/d420/tray/status':self.latest['tray']=json.loads(msg.data);row['data']=self.latest['tray']
            elif topic=='/odom':
                p=msg.pose.pose;v=msg.twist.twist;tray=self.latest.get('tray',{});s=self.status
                row['telemetry']={'utc':utc,'mono':mono,'ros_stamp':row['ros_stamp'],'x':p.position.x,'y':p.position.y,
                    'v':v.linear.x,'angular_v':v.angular.z,'requested_v':s.get('requested_v'),'final_v':self.latest.get('motor',{}).get('final_v'),
                    'state':s.get('state'),'segment':s.get('segment','forward'),
                    'roll_deg':tray.get('roll_deg') if tray.get('imu_fresh') else None,
                    'pitch_deg':tray.get('pitch_deg') if tray.get('imu_fresh') else None,
                    'roll_pwm_command_us':tray.get('roll_pwm'),'pitch_pwm_command_us':tray.get('pitch_pwm'),
                    'servo_measured_angle':None}
                self.latest['odom_utc']=utc
            elif not ('Image' in typ or 'PointCloud' in typ):row['data']=message_to_ordereddict(msg)
            # Raw CDR payload, including image arrays, is always stored in rosbag2.
            with self.record_lock:
                if self.recorder:self.recorder.append(topic,typ,raw,int(utc*1e9),json_safe(row))
        except Exception as e:self.error='Message decode/log error: '+topic+' '+type(e).__name__

    def request(self,op,**data):
        import secrets
        key=secrets.token_hex(16);m=self.String();m.data=json.dumps({'op':op,'request_id':key,'utc':time.time(),**data},allow_nan=False)
        self.publisher.publish(m)
        if op=='beat':return {'ok':True}
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            reply=self.status.get('reply',{})
            if reply.get('request_id')==key:
                if not reply.get('ok'):raise ValueError(reply.get('error','Jetson rejected'))
                return reply
            time.sleep(.01)
        # Never retry a START automatically; watchdog handles uncertain acceptance.
        raise ValueError('Jetson acknowledgement timeout; no automatic start retry')

    def start_record(self,path):
        if self.recorder:raise ValueError('Already recording')
        if time.time()-self.latest.get('odom_utc',0)>.3:raise ValueError('Fresh odometry required for raw recording')
        # Freeze type inventory per run. New topics discovered later are reported for next run.
        self.recorder=RawRecorder(path,[(t,types[0]) for t,types in self.inventory if t in self.subs])
        dump(path/'capture_exclusions.json',{'exclusive_command_topics':sorted(EXCLUSIVE_COMMAND_TOPICS),
             'bulk_message_types':sorted(BULK_TYPES),'experiment_video':'C920 on Windows; D435 depth observations and scan retained as numerical messages',
             'reason':'Do not add subscribers to channels guarded by existing single-owner safety checks',
             'raw_commands':'Owner-side motor_output mirror required; missing mirror means unavailable, never reconstructed as actual motor feedback'})

    def stop_record(self):
        with self.record_lock:r=self.recorder;self.recorder=None
        if r:r.close()
