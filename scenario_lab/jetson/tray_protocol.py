"""Existing ESP32 text protocol. Parsing never enables servos or saves zeros."""
import math
import re
import time

COMMANDS = {'1','2','g','v','h','x','z','s','u','i','Q','W','E','R'}
BOUNDARY = '-------------------------'


def prediction_command(roll, pitch):
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>8 for v in (roll,pitch)):
        raise ValueError('예측 목표각은 ±8° 이내여야 합니다.')
    return f'@T,{roll:.3f},{pitch:.3f}\n'


def is_prediction(command):
    if not isinstance(command,str):return False
    match=re.fullmatch(r'@T,(-?\d+(?:\.\d{1,3})?),(-?\d+(?:\.\d{1,3})?)\n',command)
    return bool(match and all(math.isfinite(float(x)) and abs(float(x))<=8 for x in match.groups()))


class StatusParser:
    def __init__(self):
        self.buffer = ''
        self.frame = None

    def feed(self, data):
        self.buffer += data.decode('utf-8',errors='replace')
        if len(self.buffer)>16384:
            self.buffer='';self.frame=None
            return []
        frames=[]
        while '\n' in self.buffer:
            line,self.buffer=self.buffer.split('\n',1)
            line=line.strip()
            if line==BOUNDARY:
                if self.frame is not None and self.complete(self.frame):frames.append(self.frame)
                # Every boundary can start the next frame. This also recovers
                # when USB was opened halfway through a periodic status block.
                self.frame={'protocol':1}
                continue
            if self.frame is None: continue
            # v4 reports both servos and the mode on one line.
            combined=re.fullmatch(r'ROLL=(\d+) \((ON|OFF)\)  PITCH=(\d+) \((ON|OFF)\)  MODE=([0-6])',line)
            if combined:
                self.frame.update(roll_pwm=int(combined[1]),roll_enabled=combined[2]=='ON',
                                  pitch_pwm=int(combined[3]),pitch_enabled=combined[4]=='ON',
                                  mode=int(combined[5]),firmware='APPARENT_V4')
            match=re.fullmatch(r'(ROLL|PITCH)=(\d+) \((ON|OFF)\)',line)
            if match:
                key=match[1].lower()
                self.frame[key+'_pwm']=int(match[2]);self.frame[key+'_enabled']=match[3]=='ON'
            match=re.fullmatch(r'MODE=([0-7])',line)
            if match: self.frame['mode']=int(match[1])
            if line=='PROTO=2':self.frame['protocol']=2
            if line=='FIRMWARE=APPARENT_V4_FF':self.frame['firmware']='APPARENT_V4_FF'
            if line=='PREDICT_MODE=7':self.frame['predictive_mode']=7
            match=re.fullmatch(r'PWM_DIAG R_PIN=(\d+) P_PIN=(\d+) R_ATT=([01]) P_ATT=([01]) R_HZ=(\d+) P_HZ=(\d+) R_DUTY=(\d+) P_DUTY=(\d+) R_BITS=(\d+) P_BITS=(\d+)',line)
            if match:
                keys=('roll_pin','pitch_pin','roll_attached','pitch_attached','roll_hz','pitch_hz',
                      'roll_duty','pitch_duty','roll_bits','pitch_bits')
                self.frame['pwm_diagnostics']=dict(zip(keys,map(int,match.groups())))
            match=re.fullmatch(r'IMU_READS=(\d+)',line)
            if match:self.frame['imu_read_count']=int(match[1])
            match=re.fullmatch(r'BNO_DIAG SYS=(\d+) SELFTEST=(\d+) ERROR=(\d+)',line)
            if match:self.frame['imu_diagnostics']=dict(zip(('system','selftest','error'),map(int,match.groups())))
            match=re.fullmatch(r'TARGET R=([+\-\d.eE]+) P=([+\-\d.eE]+)',line)
            if match:
                try:
                    values=[float(v) for v in match.groups()]
                    if all(math.isfinite(v) and abs(v)<=8 for v in values):
                        self.frame.update(target_roll_deg=values[0],target_pitch_deg=values[1])
                except ValueError:pass
            if line in ('APP_TIMEOUT=0','APP_TIMEOUT=1'):self.frame['app_timeout']=line.endswith('1')
            match=re.fullmatch(r'(?:IMU|TRAY TILT \(중력 기준\)) R=([+\-\d.eE]+) deg \| P=([+\-\d.eE]+) deg',line)
            if match:
                try:
                    roll,pitch=float(match[1]),float(match[2])
                    if all(math.isfinite(v) and abs(v)<=360 for v in [roll,pitch]):
                        self.frame.update(roll_deg=roll,pitch_deg=pitch,imu_fresh=True)
                except ValueError: pass
            if line=='IMU DATA NOT READY': self.frame.update(roll_deg=None,pitch_deg=None,imu_fresh=False)
            if re.fullmatch(r'IMU RAW R=[+\-\d.eE]+ deg \| P=[+\-\d.eE]+ deg \(영점 미설정\)',line):
                self.frame.update(roll_deg=None,pitch_deg=None,imu_fresh=True)
            match=re.fullmatch(r'HOME R=(\d+) P=(\d+)(?: \| INTEG R=[+\-\d.eE]+ P=[+\-\d.eE]+ us)?',line)
            if match: self.frame.update(roll_home=int(match[1]),pitch_home=int(match[2]))
            match=re.fullmatch(r'ZERO=([01]) DIR=([01])(?: \(dirR=-?1 dirP=-?1\))?',line)
            if match: self.frame.update(zero_saved=match[1]=='1',direction_checked=match[2]=='1')
        return frames

    @staticmethod
    def complete(frame):
        required={'roll_pwm','pitch_pwm','roll_enabled','pitch_enabled','mode','roll_deg','pitch_deg',
                  'imu_fresh','roll_home','pitch_home','zero_saved','direction_checked'}
        mode_valid=(frame.get('mode',-1)<=5 or frame.get('mode')==6 and frame.get('firmware')=='APPARENT_V4'
                    or frame.get('mode') in (6,7) and frame.get('firmware')=='APPARENT_V4_FF' and frame.get('predictive_mode')==7)
        return mode_valid and required<=frame.keys() and all(1100<=frame[k]<=1900 for k in ['roll_pwm','pitch_pwm','roll_home','pitch_home'])


def check_command(command,status):
    predictive=is_prediction(command)
    if not isinstance(command,str) or (command not in COMMANDS and not predictive): raise ValueError('허용되지 않은 트레이 명령입니다.')
    if not status.get('connected'): raise ValueError('최신 ESP32 상태를 먼저 수신해야 합니다.')
    if predictive and status.get('protocol')!=2:
        raise ValueError('예측 모드는 앱용 ESP32 펌웨어(PROTO=2) 업로드가 필요합니다.')
    if (command in {'g','h','z'} or predictive) and not (status.get('roll_enabled') and status.get('pitch_enabled') and status.get('imu_fresh')):
        raise ValueError('두 서보 활성화와 정상 IMU 데이터가 필요합니다.')
    if command=='v' and not (status.get('zero_saved') and status.get('imu_fresh')):
        raise ValueError('수평 영점을 먼저 저장해 주세요.')
    if (command in {'h','z'} or predictive) and not (status.get('zero_saved') and status.get('direction_checked')):
        raise ValueError('영점 저장과 실제 보정 방향 확인을 먼저 완료해 주세요.')
    if command=='z' and status.get('mode')!=0:raise ValueError('PWM 고정 모드에서만 영점 홈으로 복귀할 수 있습니다.')
    if command in {'Q','W','E','R'}:
        if status.get('mode')!=0:raise ValueError('실행 안함(PWM 유지)으로 바꾼 뒤 미세 조정해 주세요.')
        if not status.get('roll_enabled' if command in {'Q','W'} else 'pitch_enabled'):
            raise ValueError('해당 축의 서보를 먼저 켜 주세요.')


class TraySession:
    """Transport state machine, tested with a fake serial port. Automatic writes: p only."""
    def __init__(self,port,identity=None):
        self.port=port;self.identity=identity or {};self.parser=StatusParser()
        self.latest={};self.last_frame=0.;self.last_poll=0.;self.pending=[];self.seen=[]
        self.error='ESP32 상태 대기';self.last_request={};self.target=None
        import secrets
        self.monitor_session=secrets.token_hex(8);self.frame_sequence=0

    def status(self,now=None,wall=None):
        now=time.monotonic() if now is None else now
        wall=time.time() if wall is None else wall
        age=now-self.last_frame if self.last_frame else None
        return {**self.latest,'connected':bool(self.port.is_open and age is not None and age<1.5),
                'status_age_s':age,'stamp_unix':wall,'identity':self.identity,
                'monitor_session':self.monitor_session,'frame_sequence':self.frame_sequence,
                'sample_unix':wall-age if age is not None else None,
                'port':getattr(self.port,'port',''),'error':self.error,'last_request':self.last_request}

    def request(self,request,now=None,wall=None):
        now=time.monotonic() if now is None else now
        wall=time.time() if wall is None else wall
        command=request['command'];issued=request['issued_at'];nonce=request['nonce']
        if not isinstance(issued,(int,float)) or isinstance(issued,bool) or not -.15<=wall-issued<=.75:
            raise ValueError('오래된 트레이 명령을 거절했습니다.')
        if not isinstance(nonce,str) or not 8<=len(nonce)<=80 or nonce in self.seen:
            raise ValueError('중복/잘못된 트레이 요청입니다.')
        check_command(command,self.status(now,wall))
        if len(self.pending)>=4: raise ValueError('트레이 요청 처리 중입니다.')
        self.seen.append(nonce);del self.seen[:-100]
        if is_prediction(command):self.target=(command,issued,nonce)
        else:
            self.target=None
            self.pending.append((command,issued,nonce))

    def tick(self,now=None,wall=None):
        now=time.monotonic() if now is None else now
        wall=time.time() if wall is None else wall
        for frame in self.parser.feed(self.port.read(min(self.port.in_waiting,4096))):
            self.latest=frame;self.last_frame=now;self.error='';self.frame_sequence+=1
        if self.pending:
            command,issued,nonce=self.pending.pop(0)
            if wall-issued<=.75:
                check_command(command,self.status(now,wall))
                self.port.write((b'\n' if self.latest.get('protocol')==2 else b'')+command.encode('ascii'))
                self.last_request={'nonce':nonce,'command':command,'state':'sent_not_firmware_ack'}
            else:
                self.error='만료된 트레이 요청을 버렸습니다.'
        elif self.target:
            command,issued,nonce=self.target;self.target=None
            if -.15<=wall-issued<=.25:
                check_command(command,self.status(now,wall));self.port.write(b'\n'+command.encode('ascii'))
                self.last_request={'nonce':nonce,'command':command,'state':'sent_not_firmware_ack'}
            else:self.error='만료된 예측 목표각을 버렸습니다.'
        if now-self.last_poll>=.5:
            self.port.write(b'\np' if self.latest.get('protocol')==2 else b'p');self.last_poll=now
        return self.status(now,wall)
