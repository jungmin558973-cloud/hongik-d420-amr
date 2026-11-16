"""Single-writer route target lease, consumed by the existing ESP32 USB owner."""
import math,time,hashlib
from tray_protocol import check_command,is_prediction

class RouteTrayReceiver:
    TTL=.30
    def __init__(self,session):
        self.session=session;self.lease=None;self.seq=-1;self.last_at=0.;self.revoked=[];self.reason='대기';self.last_lease=None
    def revoke(self,reason,now,wall,level=False):
        if self.lease:
            self.last_lease=self.lease;self.revoked.append(self.lease);self.revoked=self.revoked[-128:]
        self.lease=None;self.session.target=None;self.reason=reason
        if level:
            status=self.session.status(now,wall)
            # Never re-enable an explicitly disabled/manual output or stale IMU.
            if status.get('mode') in (4,5,7):
                try:self.session.request(dict(command='h',issued_at=wall,nonce='route-end-'+str(time.monotonic_ns())),now,wall)
                except ValueError:pass
    def receive(self,d,now=None,wall=None):
        now=time.monotonic() if now is None else now;wall=time.time() if wall is None else wall
        if not isinstance(d,dict):raise ValueError('경로 목표 형식 오류')
        lease=d.get('route_id');seq=d.get('seq');stamp=d.get('issued_at');op=d.get('op')
        if not isinstance(lease,str) or not 16<=len(lease)<=80 or type(seq) is not int or seq<0 or type(stamp) not in (int,float) or not math.isfinite(stamp) or not -.05<=wall-stamp<=.20:raise ValueError('지연/잘못된 경로 트레이 목표')
        if lease in self.revoked:raise ValueError('종료된 경로 목표')
        if self.lease and self.lease!=lease:raise ValueError('다른 경로가 트레이를 사용 중입니다.')
        if self.lease==lease and seq<=self.seq:raise ValueError('역순/중복 경로 목표')
        if op=='release':
            if self.lease==lease:self.revoke('경로 종료 · 수평 복귀',now,wall,level=True)
            else:self.revoked.append(lease);self.revoked=self.revoked[-128:]
            return
        command=d.get('command')
        if op!='target' or not is_prediction(command):raise ValueError('경로 목표 명령 오류')
        status=self.session.status(now,wall);check_command(command,status)
        if status.get('mode') not in (4,5,7):raise ValueError('먼저 ESP32 수평/예측 제어를 선택하세요.')
        nonce='rt-'+hashlib.sha256(f'{lease}:{seq}'.encode()).hexdigest()
        self.session.request(dict(command=command,issued_at=stamp,nonce=nonce),now,wall)
        self.lease=self.last_lease=lease;self.seq=seq;self.last_at=now;self.reason='경로 예측 목표 전송'
    def tick(self,now=None,wall=None):
        now=time.monotonic() if now is None else now;wall=time.time() if wall is None else wall
        if self.lease and now-self.last_at>self.TTL:self.revoke('경로 목표 수신 만료',now,wall,level=True)
    def status(self,now=None):
        now=time.monotonic() if now is None else now
        return dict(active=bool(self.lease),route_id=self.lease or self.last_lease,seq=self.seq,
                    age_s=max(0.,now-self.last_at) if self.last_at else None,reason=self.reason)
