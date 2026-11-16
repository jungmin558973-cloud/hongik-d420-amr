"""Jetson-owned bounded L route; existing serial bridge remains sole motor owner."""
from pathlib import Path
import sys,time,secrets,json,math,atexit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import Pose
from depth_route import DepthRoute
from route_prediction import RoutePredictor,RoutePredictionConfig
from jetson.tray_protocol import check_command,prediction_command
from experiment_store import ExperimentRecorder,SampleBuilder
from release_info import RELEASE
from experiment_cases import parse_case

class RouteHost:
 def __init__(self):
  self.engine=None;self.route_id=None;self.session=secrets.token_hex(8)
  self.depth={'version':1,'ready':False,'reason':'깊이 관측 대기'};self.depth_at=0.
  self.reply={};self.seen={};self.cancelled=set();self.last_tick=None;self.trial=True
  self.predictor=None
  self.tray_level=False;self.tray_fixed=False
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
    if type(speed) not in (int,float) or not math.isfinite(speed) or not .03<=speed<=DepthRoute.MAX_SPEED:raise ValueError('L자 시험 속도는 3~40cm/s입니다.')
    if trial:speed=min(speed,.12)
    brake=data.get('brake',True)
    if type(brake) is not bool:raise ValueError('급정지 설정 오류')
    predictive=data.get('tray_predictive',False)
    if type(predictive) is not bool:raise ValueError('트레이 예측 활성화 설정 오류')
    level=data.get('tray_level',False)
    if type(level) is not bool:raise ValueError('트레이 수평 유지 설정 오류')
    prediction_config=RoutePredictionConfig.parse(data.get('tray_prediction',{})) if predictive else None
    case=parse_case(data['experiment_case'],speed) if data.get('experiment_case') is not None else None
    if case and (trial or not brake or predictive!=(case['tray']=='PREDICTIVE') or level!=(case['tray']!='OFF')):raise ValueError('실험 조건과 제어 모드가 일치하지 않습니다.')
    engine=DepthRoute();engine.arm(data.get('first'),data.get('second'),speed,now,pose,self.depth,wall,brake=brake and not trial,turn_extension=data.get('turn_extension',0.),smooth=case['smooth'] if case else False,experiment_case=case,options=data.get('experiment_options'))
    self.engine=engine;self.route_id=rid;self.last_tick=now
    self.predictor=RoutePredictor(prediction_config) if predictive else None
    self.tray_level=level;self.tray_fixed=bool(case and case['tray']=='OFF')
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
  if self.predictor:
   self.predictor.step(e,pose,now)
   if e.history:e.history[-1]['tray_prediction']=dict(self.predictor.diagnostics)
  return command
 def manual_takeover(self):
  if self.engine and self.engine.active:self.engine.stop('수동 명령으로 자율 주행 종료')
 def state(self,now,wall):
  snapshot=self.engine.snapshot(now) if self.engine else None
  if snapshot:
   snapshot.pop('chart',None)
   snapshot['tray_prediction']={'enabled':bool(self.predictor),**(self.predictor.diagnostics if self.predictor else {})}
   snapshot['tray_level_required']=self.tray_level
  return {'version':1,'release':RELEASE,'bridge_session':self.session,'route_id':self.route_id,
   'published_unix':wall,'reply':self.reply,'engine':snapshot,
   'trial':self.trial,'trial_limits':{'distance_m':.60,'duration_s':10.,'speed_m_s':.12}}

class LocalRouteAdapter:
 def __init__(self,node,recorder=None):
  from std_msgs.msg import String
  from rclpy.qos import QoSProfile,ReliabilityPolicy
  self.node=node;self.host=RouteHost();self.String=String
  self.recorder=recorder or ExperimentRecorder();self.record_id=None;self.recorded_route=None;self.record_seq=0;self.sample_builder=SampleBuilder()
  atexit.register(self.close)
  self.pose=Pose();self.live=False;self.last_publish=0.
  self.tray={};self.tray_at=0.;self.target_seq=0;self.target_route=None;self.target_started=0.;self.target_released=True;self.target_acquired=False
  self.pub=node.create_publisher(String,'/d420/local_route/status',10)
  self.tray_pub=node.create_publisher(String,'/d420/tray/route_target',QoSProfile(depth=1,reliability=ReliabilityPolicy.BEST_EFFORT))
  node.create_subscription(String,'/d420/tray/status',self.tray_status,1)
  node.create_subscription(String,'/d420/local_route/request',self.request,10)
  node.create_subscription(String,'/d420/depth_route/observation',self.observe,QoSProfile(depth=1,reliability=ReliabilityPolicy.BEST_EFFORT))
 def tray_status(self,msg):
  try:
   if len(msg.data)>16384:return
   d=json.loads(msg.data)
   if isinstance(d,dict):self.tray=d;self.tray_at=time.monotonic()
  except (ValueError,TypeError):pass
 def tray_reason(self,now,wall,predictive=True,fixed=False):
  if self.node.count_publishers('/d420/tray/status')!=1:return '트레이 상태 발행자가 하나여야 합니다.'
  stamp=self.tray.get('stamp_unix');age=self.tray.get('status_age_s')
  if now-self.tray_at>.75 or type(stamp) not in (int,float) or not math.isfinite(stamp) or not -.05<=wall-stamp<=.75 or type(age) not in (int,float) or not 0<=age<=.9:return 'ESP32 상태/USB 수신 지연'
  try:check_command(prediction_command(0,0) if predictive else 'h',self.tray)
  except ValueError as exc:return str(exc)
  if self.tray.get('mode') not in ((0,) if fixed else (4,self.tray.get('predictive_mode',5)) if predictive else (4,)):return 'ESP32 실험 제어 모드가 변경됐습니다.'
  if fixed and any(type(self.tray.get(k)) not in (int,float) or type(self.tray.get(h)) not in (int,float) or abs(self.tray[k]-self.tray[h])>2 for k,h in [('roll_pwm','roll_home'),('pitch_pwm','pitch_home')]):return '무보정 실험의 영점 홈 PWM이 변경됐습니다.'
  if predictive and self.node.count_subscribers('/d420/tray/route_target')!=1:return '새 트레이 모니터의 경로 목표 수신자를 확인하세요.'
  return ''
 def send_target(self,op,wall):
  if not self.target_route:return
  self.target_seq+=1;d=dict(op=op,route_id=self.target_route,seq=self.target_seq,issued_at=wall)
  if op=='target':d['command']=prediction_command(self.host.predictor.roll,self.host.predictor.pitch)
  msg=self.String();msg.data=json.dumps(d,allow_nan=False);self.tray_pub.publish(msg)
 def release_target(self):
  if self.host.predictor and not self.target_released:self.send_target('release',time.time());self.target_released=True
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
   if d.get('op')=='start' and not self.recorder.status()['ready']:
    self.host.reply={'request_id':d.get('request_id'),'accepted':False,'reason':'실험 DB 준비/오류: '+self.recorder.error};self.publish();return
   if d.get('op')=='start' and (d.get('experiment_case') is not None or d.get('tray_predictive') is True or d.get('tray_level') is True):
    case=parse_case(d['experiment_case'],d.get('speed')) if d.get('experiment_case') is not None else None
    reason=self.tray_reason(time.monotonic(),time.time(),predictive=d.get('tray_predictive') is True,fixed=bool(case and case['tray']=='OFF'))
    if reason:
     self.host.reply={'request_id':d.get('request_id'),'accepted':False,'reason':reason};self.publish();return
   if d.get('op')=='start' and self.record_id and self.host.engine and not self.host.engine.active:
    self.record_tick(time.monotonic(),time.time(),(0.,0.),self.live,None)
   self.host.request(d,self.pose,self.live,time.monotonic(),time.time())
   if d.get('op')=='start' and self.host.reply.get('accepted'):
    if self.host.engine.active and self.recorded_route!=self.host.route_id:
     try:
      self.record_id=self.recorder.begin('depth-route-trial' if self.host.trial else 'depth-route','real',{'software_release':RELEASE,**d},run_id=self.host.route_id)
      self.recorded_route=self.host.route_id;self.record_seq=0;self.sample_builder=SampleBuilder()
     except ValueError as exc:
      self.host.engine.stop(str(exc));self.host.reply.update(accepted=False,reason=str(exc));self.publish();return
    self.node.cmd_v=self.node.cmd_w=0.;self.node.last_cmd_time=0.
    if self.target_route!=self.host.route_id:
     self.target_route=self.host.route_id;self.target_seq=0;self.target_started=time.monotonic();self.target_released=False;self.target_acquired=False
   if d.get('op')=='stop' and self.host.reply.get('accepted'):self.release_target()
   self.publish()
  except (ValueError,TypeError):pass
 def manual_takeover(self):self.host.manual_takeover();self.release_target()
 def close(self):
  if self.recorder.closed:return
  if self.host.engine and self.record_id:
   self.host.engine.stop('차체 제어 프로세스 종료')
   self.record_tick(time.monotonic(),time.time(),(0.,0.),False,None)
  self.recorder.close()
 def record_tick(self,now,wall,command,live,decoder):
  e=self.host.engine
  if not e or not self.record_id:return
  try:
   snapshot=e.snapshot(now)
   snapshot.pop('chart',None)
   sample_live=live and all(math.isfinite(v) for v in vars(self.pose).values())
   row=self.sample_builder.build(now,wall,now-e.started,e.state,command or (0.,0.),e.pose,self.tray,
       self.host.predictor.diagnostics if self.host.predictor else ({'roll_deg':0.,'pitch_deg':0.,'source':'LEVEL'} if self.host.tray_level else {}),live=sample_live,
       feedback_at=getattr(decoder,'last_velocity',None),route=snapshot,
       depth={k:v for k,v in self.host.depth.items() if k!='points'})
   self.record_seq+=1;self.recorder.sample(self.record_id,self.record_seq,row)
   if not e.active:self.recorder.finish(self.record_id,snapshot);self.record_id=None
  except (ValueError,TypeError) as exc:
   e.stop('실험 DB 기록 중단: '+str(exc));self.recorder.error=str(exc)
 def tick(self,now,live,x,y,decoder):
  self.live=live
  if live:self.pose=Pose(x,y,decoder.yaw,decoder.v[0],decoder.v[2])
  if self.node.count_publishers('/d420/depth_route/observation')>1:
   if self.host.engine:self.host.engine.stop('깊이 관측 발행자 중복')
  e=self.host.engine
  was_active=bool(e and e.active)
  if e and e.active and self.recorder.error:e.stop('실험 DB 저장 오류: '+self.recorder.error)
  if e and e.active and self.host.tray_fixed:
   reason=self.tray_reason(now,time.time(),predictive=False,fixed=True)
   if reason:e.stop('무보정 실험 상태 중단: '+reason)
  if e and e.active and self.host.tray_level and not self.host.predictor:
   reason=self.tray_reason(now,time.time(),predictive=False)
   if reason:e.stop('수평 유지 연동 중단: '+reason)
  if e and e.active and self.host.predictor:
   reason=self.tray_reason(now,time.time());ack=self.tray.get('route_control',{})
   age=ack.get('age_s')
   acknowledged=bool(ack.get('active') and ack.get('route_id')==self.target_route and type(age) in (int,float) and age+now-self.tray_at<=.4 and self.tray.get('mode')==self.tray.get('predictive_mode',5) and not self.tray.get('app_timeout',False))
   if not reason and not self.target_acquired and not acknowledged and now-self.target_started<=1.5:
    # Acquire with a zero target; do not move until the actual firmware reports its predictive mode.
    self.send_target('target',time.time());self.host.last_tick=now;e.last_tick=now
    e.message='ESP32 예측 모드·USB 전달 확인 중 · 차체 정지';self.record_tick(now,time.time(),(0.,0.),live,decoder);self.publish();return (0.,0.)
   if not reason and not acknowledged:reason='ESP32 예측 모드/목표 전달 확인이 끊겼습니다.'
   if acknowledged:self.target_acquired=True
   if reason:e.stop('트레이 연동 중단: '+reason)
  command=self.host.tick(self.pose,live,now,time.time())
  if e and self.host.predictor:
   if e.active:self.send_target('target',time.time())
   else:self.release_target()
  if was_active and not e.active and command is None:command=(0.,0.)
  self.record_tick(now,time.time(),command,live,decoder)
  if was_active and not e.active:command=(0.,0.)
  if now-self.last_publish>=.1:self.publish()
  return command
 def publish(self):
  self.last_publish=time.monotonic();m=self.String();state=self.host.state(self.last_publish,time.time())
  state['recording']={**self.recorder.status(),'run_id':self.record_id,'samples':self.record_seq}
  m.data=json.dumps(state,allow_nan=False);self.pub.publish(m)
