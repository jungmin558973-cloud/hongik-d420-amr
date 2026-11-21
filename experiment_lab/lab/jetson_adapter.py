"""Loaded INSIDE the existing serial owner; never opens a serial device.

New /d420/experiment/* topics are explicitly new extension interfaces.
"""
import json
import math
import os
from pathlib import Path
import time
from .core import Config,Controller,Observation,Validation,TrayPredictor


def validation_from_file():
    path=os.environ.get('D420_VALIDATION_FILE')
    if not path:return Validation()
    d=json.loads(Path(path).read_text(encoding='utf-8'))
    if not d.get('evidence'):raise ValueError('Commissioning evidence required')
    return Validation(**d)


class ExperimentAdapter:
    def __init__(self,node,legacy):
        from std_msgs.msg import String
        from sensor_msgs.msg import LaserScan
        from rclpy.qos import qos_profile_sensor_data
        self.node=node;self.legacy=legacy;self.String=String
        validation=validation_from_file()
        validation.max_speed=min(validation.max_speed,getattr(node,'max_linear_speed',.12))
        self.control=Controller(validation);self.obs=Observation(live=False,sensors=False,clear_front=False,clear_rear=False)
        self.final_v=0.;self.final_w=0.;self.last_external_nonzero=0.
        self.last_scan=0.;self.scan_front=self.scan_rear=False;self.depth={};self.depth_at=0.
        self.reply={};self.requests={};self.last_publish=0.;self.event_index=0;self.force_zero_until=0.
        self.motion_enabled=os.environ.get('D420_EXPERIMENT_MOTION')=='1'
        self.tray={};self.tray_at=0.;self.predictor=TrayPredictor();self.tray_seq=0;self.tray_acquired=False
        self.pub=node.create_publisher(String,'/d420/experiment/status',10)
        self.output_pub=node.create_publisher(String,'/d420/experiment/motor_output',100)
        self.tray_pub=node.create_publisher(String,'/d420/tray/route_target',10)
        node.create_subscription(String,'/d420/experiment/request',self.request,10)
        node.create_subscription(LaserScan,'/scan',self.scan,qos_profile_sensor_data)
        node.create_subscription(String,'/d420/depth_route/observation',self.observe,qos_profile_sensor_data)
        node.create_subscription(String,'/d420/tray/status',self.tray_status,qos_profile_sensor_data)

    def tray_status(self,m):
        try:self.tray=json.loads(m.data);self.tray_at=time.monotonic()
        except ValueError:pass

    def scan(self,m):
        # Require scan in base frame OR a valid TF transform. Never assume laser axes.
        try:
            from tf2_ros import Buffer,TransformListener
            from rclpy.time import Time
            if not hasattr(self,'tf'):
                self.tf=Buffer();self.listener=TransformListener(self.tf,self.node)
            if m.header.frame_id=='base_footprint':yaw=0.;tx=ty=0.
            else:
                t=self.tf.lookup_transform('base_footprint',m.header.frame_id,Time.from_msg(m.header.stamp)).transform
                q=t.rotation;yaw=math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z));tx=t.translation.x;ty=t.translation.y
            counts=[0,0];clear=[True,True]
            margin=.12+abs(self.obs.v)*.5+self.obs.v**2/(2*.15)
            for i,r in enumerate(m.ranges):
                a=m.angle_min+i*m.angle_increment+yaw
                # Infinite returns do not certify coverage. Require finite observed range.
                if not math.isfinite(r) or not m.range_min<r<m.range_max:continue
                side=0 if math.cos(a)>=0 else 1
                if abs(math.sin(a))<.65:counts[side]+=1
                x=tx+r*math.cos(a);y=ty+r*math.sin(a)
                if abs(y)<.27 and -.22-margin<x<.282+margin:clear[0 if x>=0 else 1]=False
            stamp=m.header.stamp.sec+m.header.stamp.nanosec/1e9
            if not -.1<=time.time()-stamp<.3:raise ValueError('stale scan')
            self.scan_front=clear[0] and counts[0]>=15;self.scan_rear=clear[1] and counts[1]>=15
            self.last_scan=time.monotonic()
        except Exception:self.scan_front=self.scan_rear=False

    def observe(self,m):
        try:self.depth=json.loads(m.data);self.depth_at=time.monotonic()
        except ValueError:pass

    def external_command(self,v,w):
        if v==0 and w==0:
            if self.control.active:
                self.control.abort('External STOP command',time.monotonic());self.force_zero_until=time.monotonic()+.8
                self.release_tray()
            return True
        self.last_external_nonzero=time.monotonic()
        if self.control.active:
            self.control.abort('Control ownership collision: external command',time.monotonic());self.force_zero_until=time.monotonic()+.8
            self.release_tray();return False
        return not self.control.stop_latched or (v==0 and w==0)

    def manual_takeover(self):
        if self.control.active:self.control.abort('Manual takeover requested',time.monotonic());self.force_zero_until=time.monotonic()+.8
        self.legacy.manual_takeover()

    def request(self,m):
        try:
            d=json.loads(m.data);key=d['request_id'];op=d['op'];now=time.monotonic()
            if not isinstance(key,str) or not 8<=len(key)<=80:raise ValueError('request_id')
            if key in self.requests:self.reply=self.requests[key];return
            if op not in ('stop','beat') and not -.15<=time.time()-d.get('utc',0.)<=1.5:raise ValueError('Stale request')
            if op=='stop':
                self.control.abort('Operator STOP',now);self.legacy.manual_takeover();self.release_tray();self.force_zero_until=now+.8
            elif op=='beat':
                if d.get('run_id')!=self.control.run_id:raise ValueError('Run mismatch')
                self.control.beat(d['seq'],now)
            elif op=='release':
                if self.control.active or not self.obs.live or abs(self.obs.v)>.008:raise ValueError('Stop/neutral chassis first')
                self.node.cmd_v=self.node.cmd_w=0.;self.node.last_cmd_time=0.
                self.control.stop_latched=False;self.control.state='READY';self.control.owner=None
                self.control.arm_token=None;self.force_zero_until=now+.8
                self.release_tray()
            elif op=='arm':
                if not self.motion_enabled:raise ValueError('Real experiment motion disabled at Jetson process')
                if self.legacy.host.engine and self.legacy.host.engine.active:raise ValueError('Legacy route owns motor')
                if abs(self.obs.v)>.008 or now-self.last_external_nonzero<.5:raise ValueError('Manual source active / chassis moving')
                token=self.control.arm(now,d.get('checks',{}));self.reply={'request_id':key,'ok':True,'arm_token':token}
                self.requests[key]=self.reply;self.publish();return
            elif op=='start':
                if self.legacy.host.engine and self.legacy.host.engine.active:raise ValueError('Legacy route owns motor')
                c=Config.parse(d['config'])
                if c.tray!='OFF':
                    from jetson.tray_protocol import check_command,prediction_command
                    check_command(prediction_command(0,0) if c.tray=='PREDICTIVE' else 'h',self.tray)
                elif self.tray.get('connected') and self.tray.get('mode')!=0:raise ValueError('Set existing tray to OFF / PWM hold first')
                self.control.start(c,d['run_id'],d['arm_token'],now,self.obs)
                self.tray_seq=0;self.tray_acquired=False;self.predictor=TrayPredictor()
            else:raise ValueError('Unknown operation')
            self.reply={'request_id':key,'ok':True}
        except Exception as e:self.reply={'request_id':locals().get('key',''),'ok':False,'error':str(e)}
        if 'key' in locals():
            self.requests[key]=self.reply
            if len(self.requests)>2048:self.requests.pop(next(iter(self.requests)))
        self.publish()

    def send_tray(self,op,angle=0.):
        from jetson.tray_protocol import prediction_command
        self.tray_seq+=1;m=self.String()
        d={'op':op,'route_id':self.control.run_id,'seq':self.tray_seq,'issued_at':time.time()}
        if op=='target':d['command']=prediction_command(0.,angle)
        m.data=json.dumps(d);self.tray_pub.publish(m)

    def release_tray(self):
        if hasattr(self.control,'c') and self.control.c.tray=='PREDICTIVE':self.send_tray('release')

    def tick(self,now,live,x,y,decoder):
        if live:self.obs.x=x;self.obs.y=y;self.obs.yaw=decoder.yaw;self.obs.v=decoder.v[0]
        self.obs.live=bool(live)
        depth_fresh=now-self.depth_at<.35 and self.depth.get('ready') is True and -.1<=time.time()-self.depth.get('source_unix',0)<.35
        points=self.depth.get('points',[]);margin=.4+abs(self.obs.v)*.5+self.obs.v**2/.3
        front_depth_clear=not any(0<p[0]<margin and abs(p[1])<.27 for p in points)
        scan_fresh=now-self.last_scan<.3 and self.node.count_publishers('/scan')==1
        self.obs.sensors=bool(depth_fresh and scan_fresh and self.node.count_publishers('/d420/depth_route/observation')==1)
        self.obs.clear_front=bool(self.scan_front and front_depth_clear)
        self.obs.clear_rear=bool(self.scan_rear and scan_fresh)
        self.obs.tray_ok=now-self.tray_at<.5 and self.tray.get('connected') and self.tray.get('imu_fresh')
        was=self.control.active
        if was:
            if self.node.count_publishers('/d420/experiment/request')!=1:
                self.control.abort('Experiment command publisher missing/duplicate',now)
            if self.control.c.tray=='LEVEL' and self.tray.get('mode')!=4:self.obs.tray_ok=False
            if self.control.c.tray=='PREDICTIVE':
                ack=self.tray.get('route_control',{})
                self.tray_acquired=bool(ack.get('active') and ack.get('route_id')==self.control.run_id and ack.get('age_s',99)<.35 and self.tray.get('mode')==self.tray.get('predictive_mode',5))
                if self.control.state=='RECORDING':self.send_tray('target',0.)
                elif not self.tray_acquired:self.obs.tray_ok=False
            command=self.control.tick(now,self.obs)
            if self.control.active and self.control.c.tray=='PREDICTIVE':
                t=now-self.control.phase_at
                active=self.control.state in ('MOVING','BRAKING','STOPPING','RETURNING')
                # Recovery is conservative LEVEL-only; no experimental feedforward in reverse.
                eta=(self.control.c.distance-self.control.along(self.obs))/max(.01,abs(self.obs.v))
                angle=self.predictor.step(self.control.c,self.control.profile,t,.05,active and self.control.segment=='forward',brake_eta=eta,measured_v=self.obs.v)
                self.send_tray('target',angle)
            if not self.control.active:self.release_tray()
            result=(command,0.)
        elif self.control.stop_latched or now<self.force_zero_until:result=(0.,0.)
        else:result=self.legacy.tick(now,live,x,y,decoder)
        if now-self.last_publish>.05:self.publish()
        return result

    def publish(self):
        self.last_publish=time.monotonic();m=self.String();c=self.control
        d={'utc':time.time(),'mono':self.last_publish,'state':c.state,'run_id':c.run_id,
           'segment':c.segment,'reply':self.reply,'x':self.obs.x,'y':self.obs.y,'v':self.obs.v,
           'requested_v':c.command,'final_v':self.final_v,
           'checks':vars(self.obs),'reason':c.reason,'motion_enabled':self.motion_enabled,
           'validation':vars(c.validation),'events':c.events[-50:],'tray':self.tray,
           'capabilities':{'s_curve_strength':True}}
        m.data=json.dumps(d,allow_nan=False);self.pub.publish(m)

    def close(self):self.control.abort('Serial owner closing',time.monotonic());self.release_tray();self.legacy.close()

    def motor_output(self,v,w,packet,live,cmd_live):
        # Values encoded by the existing mm/s and 0.01rad/s packet protocol.
        self.final_v=round(v*1000)/1000.;self.final_w=round(w*100)/100.
        m=self.String();m.data=json.dumps({'utc':time.time(),'mono':time.monotonic(),
            'requested_v':v,'requested_w':w,'final_v':self.final_v,'final_w':self.final_w,
            'packet_hex':packet.hex(),'telemetry_live':live,'command_live':cmd_live,
            'owner':'experiment' if self.control.active else 'safety' if self.control.stop_latched else 'legacy',
            'physical_brake_feedback':None},allow_nan=False)
        self.output_pub.publish(m)
