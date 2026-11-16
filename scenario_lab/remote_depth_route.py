"""Read-only proxy for the route running inside Jetson's serial owner."""
import time
from engine import Engine

class RemoteDepthRoute(Engine):
 def __init__(self,route_id):
  super().__init__();self.kind='depth-route';self.mode='real';self.active=True
  self.route_id=route_id;self.state='countdown';self.remote=None;self.started=time.monotonic()
  self.message='Jetson 경로 상태 확인 중';self.stale=False;self.stop_requested=False;self.bridge_session=None;self.trial=True
 def update(self,data,now):
  if data.get('transport_age_ms',99999)<=800 and self.bridge_session and data.get('bridge_session')!=self.bridge_session:
   self.active=False;self.state='stopped';self.command=(0.,0.);self.message='Jetson 제어 세션이 재시작됐습니다.';return
  if data.get('route_id')!=self.route_id or not data.get('engine') or data.get('transport_age_ms',99999)>800:
   self.stale=True;self.message='화면 연결 지연 · Jetson 센서 기반 주행 상태 확인 중';return
  self.remote=data['engine'];self.recording=data.get('recording',{});self.stale=False;self.bridge_session=data.get('bridge_session');self.trial=data.get('trial',True)
  self.active=bool(self.remote['active']);self.message=('정지 요청 전송 중 · Jetson 확인 대기' if self.stop_requested and self.active else self.remote['message'])
  self.command=(0.,0.)  # The proxy NEVER publishes the remote motor command.
  if not self.active:self.state='done' if self.remote['state']=='done' else 'stopped'
  self.history.append({'t':self.remote['elapsed'],**self.remote['pose'],'v':self.remote['v'],'w':self.remote['w'],
   'cmd_v':self.remote['command'][0],'cmd_w':self.remote['command'][1],
   'phase':self.remote['state'],'remote':self.remote})
  if len(self.history)>5000:del self.history[:-5000]
 def stop(self,reason='사용자가 중지했습니다.'):
  self.stop_requested=True;self.command=(0.,0.)
  self.message='정지 요청 전송 중 · Jetson 확인 대기'
 def snapshot(self,now):
  d=dict(self.remote) if self.remote else super().snapshot(now)
  d.update(kind='depth-route',mode='real',active=self.active,message=self.message,recording=getattr(self,'recording',{}),
   execution_host='Jetson',display_connection_stale=self.stale,route_id=self.route_id,stop_confirmation_pending=self.stop_requested and self.active,trial=self.trial,trial_limits={'distance_m':.6,'duration_s':10.,'speed_m_s':.12} if self.trial else None)
  return d
