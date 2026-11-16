"""Jetson-owned bounded L route; existing serial bridge remains sole motor owner."""
from pathlib import Path
import sys,time,secrets,json,math
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import Pose
from depth_route import DepthRoute

class RouteHost:
 def __init__(self):
  self.engine=None;self.route_id=None;self.session=secrets.token_hex(8)
  self.depth={'version':1,'ready':False,'reason':'깊이 관측 대기'};self.depth_at=0.
  self.reply={};self.seen={};self.cancelled=set();self.last_tick=None;self.trial=True
 def observation(self,data,now):self.depth=data;self.depth_at=now
 def request(self,data,pose,live,now,wall):
  key=data.get('request_id');rid=data.get('route_id');op=data.get('op')
  if not isinstance(key,str) or not 8<=len(key)<=80:return
  if key in self.seen:self.reply=self.seen[key];return
  reply={'request_id':key,'accepted':False,'reason':''}
  try:
   stamp=data.get('created_unix')
   if op!='stop' and (type(stamp) not in (int,float) or not math.isfinite(stamp) or not -.15<=wall-stamp<=2.):
    raise ValueError('주행 요청 시각이 지연됐습니다.')
   if op=='stop':
    if rid:self.cancelled.add(rid)
    if rid and self.route_id and rid!=self.route_id:raise ValueError('이전 경로의 정지 요청입니다.')
    if self.engine:self.engine.stop('사용자가 중지했습니다.')
   elif op=='start':
    if not isinstance(rid,str) or not 16<=len(rid)<=80:raise ValueError('경로 세션 오류')
    if rid in self.cancelled:raise ValueError('이미 취소한 경로입니다.')
    if data.get('bridge_session')!=self.session:raise ValueError('Jetson 세션이 변경됐습니다.')
    if self.engine and self.engine.active:raise ValueError('Jetson에서 이전 경로가 실행 중입니다.')
    if not live:raise ValueError('최신 차체 피드백을 기다려 주세요.')
    if now-self.depth_at>.35:raise ValueError('최신 깊이 관측을 기다려 주세요.')
    trial=data.get('trial',True)
    if type(trial) is not bool:raise ValueError('검증/전체 경로 모드를 확인해 주세요.')
    speed=data.get('speed')
    if type(speed) not in (int,float) or not math.isfinite(speed) or not .03<=speed<=DepthRoute.MAX_SPEED:raise ValueError('L자 시험 속도는 3~20cm/s입니다.')
    if trial:speed=min(speed,.12)
    brake=data.get('brake',True)
    if type(brake) is not bool:raise ValueError('급정지 설정 오류')
    engine=DepthRoute();engine.arm(data.get('first'),data.get('second'),speed,now,pose,self.depth,wall,brake=brake and not trial)
    self.engine=engine;self.route_id=rid;self.last_tick=now
    self.trial=trial
   else:raise ValueError('지원하지 않는 로컬 경로 요청')
   reply['accepted']=True
  except ValueError as e:reply['reason']=str(e)
  self.reply=reply;self.seen[key]=reply
  if len(self.seen)>256:self.seen.pop(next(iter(self.seen)))
 def tick(self,pose,live,now,wall):
  e=self.engine
  if not e or not e.active:self.last_tick=now;return None
  if not live:
   e.stop('차체 센서 피드백 단절');self.last_tick=now;return (0.,0.)
  if self.last_tick is not None and now-self.last_tick>.15:
   e.pause('Jetson 제어 주기가 지연됐습니다.',now)
  self.last_tick=now
  d=dict(self.depth)
  if now-self.depth_at>.35:d.update(ready=False,fault='transport_stale',reason='Jetson 내부 깊이 관측 지연')
  e.observe(d,wall);command=e.step(now,pose)
  if self.trial and (now-e.started>=10. or e.distance>=.60 or e.state!='depth_first'):
   e.stop('첫 검증 시험 한계 도달 · 최대 60cm / 10초');return (0.,0.)
  return command
 def manual_takeover(self):
  if self.engine and self.engine.active:self.engine.stop('수동 명령으로 자율 주행 종료')
 def state(self,now,wall):
  snapshot=self.engine.snapshot(now) if self.engine else None
  if snapshot:snapshot.pop('chart',None)
  return {'version':1,'bridge_session':self.session,'route_id':self.route_id,
   'published_unix':wall,'reply':self.reply,'engine':snapshot,
   'trial':self.trial,'trial_limits':{'distance_m':.60,'duration_s':10.,'speed_m_s':.12}}

class LocalRouteAdapter:
 def __init__(self,node):
  from std_msgs.msg import String
  from rclpy.qos import QoSProfile,ReliabilityPolicy
  self.node=node;self.host=RouteHost();self.String=String
  self.pose=Pose();self.live=False;self.last_publish=0.
  self.pub=node.create_publisher(String,'/d420/local_route/status',10)
  node.create_subscription(String,'/d420/local_route/request',self.request,10)
  node.create_subscription(String,'/d420/depth_route/observation',self.observe,QoSProfile(depth=1,reliability=ReliabilityPolicy.BEST_EFFORT))
 def observe(self,msg):
  try:
   if len(msg.data)>200000:return
   d=json.loads(msg.data)
   if isinstance(d,dict):self.host.observation(d,time.monotonic())
  except (ValueError,TypeError):pass
 def request(self,msg):
  try:
   if len(msg.data)>4096:return
   d=json.loads(msg.data)
   if not isinstance(d,dict):return
   self.host.request(d,self.pose,self.live,time.monotonic(),time.time())
   if d.get('op')=='start' and self.host.reply.get('accepted'):
    self.node.cmd_v=self.node.cmd_w=0.;self.node.last_cmd_time=0.
   self.publish()
  except (ValueError,TypeError):pass
 def manual_takeover(self):self.host.manual_takeover()
 def tick(self,now,live,x,y,decoder):
  self.live=live
  if live:self.pose=Pose(x,y,decoder.yaw,decoder.v[0],decoder.v[2])
  if self.node.count_publishers('/d420/depth_route/observation')>1:
   if self.host.engine:self.host.engine.stop('깊이 관측 발행자 중복')
  command=self.host.tick(self.pose,live,now,time.time())
  if now-self.last_publish>=.1:self.publish()
  return command
 def publish(self):
  self.last_publish=time.monotonic();m=self.String();m.data=json.dumps(self.host.state(self.last_publish,time.time()),allow_nan=False);self.pub.publish(m)
