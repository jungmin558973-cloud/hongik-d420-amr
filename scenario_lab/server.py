#!/usr/bin/env python3
"""Local Three.js experiment console. Default startup never contacts the robot."""
import argparse
import asyncio
import csv
from datetime import datetime
import io
import json
import math
from pathlib import Path
import secrets
import subprocess
import time
import os
import re
import shlex
from jetson.tray_protocol import check_command, prediction_command, COMMANDS

from aiohttp import web
from engine import Config, Engine, Pose
from operator_control import Teleop, Predictor, PredictionConfig
from depth_route import DepthRoute,valid_depth
from remote_depth_route import RemoteDepthRoute
from robot_runtime import local_robot,command as robot_command
from person_follow import Follow, valid_observation
from follow_clearance import limit_command
from navigation import Navigation
from dataclasses import asdict

ROOT = Path(__file__).resolve().parent


class Lab:
    def __init__(self, allow_real=False):
        self.engine = Engine()
        self.sim = Pose()
        self.robot = None
        self.allow_real = allow_real
        self.connecting = False
        self.connection_message = '미리보기는 로봇 연결 없이 실행됩니다.'
        self.token = secrets.token_urlsafe(24)
        self.heartbeat = 0.0
        self.pending_local_route = None
        self.owner = None
        self.sockets = set()
        self.stop_until = 0.0
        self.stop_epoch = 0
        self.last_command_at = 0.0
        self.last_real_command = (0, 0)
        self.last_saved_count = 0
        self.live_reason = ''
        self.points = []
        self.last_loop = time.monotonic()
        self.run_dir = ROOT / 'runs'
        self.course_origin = None
        self.robot_pose = None
        self.camera_info = {'available': False, 'seq': 0, 'age': None}
        self.lidar_map = None
        self.course_version = 0
        self.robot_host = 'localhost' if local_robot() else os.environ.get('D420_HOST', 'seo@192.168.0.47')
        self.tray_data = {'connected':False,'error':'ESP32 미연결'}
        self.tray_ports = []
        self.tray_modes = {'preview':'OFF', 'real':'OFF'}
        self.tray_configured = {'preview':True, 'real':False}
        self.prediction_configs = {mode:PredictionConfig() for mode in self.tray_modes}
        self.predictors = {mode:Predictor() for mode in self.tray_modes}
        self.prediction_streamed = False
        self.tray_control_error = ''
        self.follow_data = {'ready':False,'reason':'YOLO 검출기를 준비해 주세요.'}
        self.follow_preparing = False
        self.mapping_mode = False

    async def prepare_follow(self):
        if not self.allow_real or self.robot is None:
            raise ValueError('실차 연결을 먼저 준비해 주세요.')
        if self.engine.active or self.follow_preparing:
            raise ValueError('주행·검출 준비 작업을 먼저 마쳐 주세요.')
        self.follow_preparing=True
        try:
            command=(['bash',str(ROOT/'jetson/follow_session.sh')] if local_robot()
                     else ['bash',str(ROOT/'prepare_follow.sh'),self.robot_host])
            result=await asyncio.to_thread(subprocess.run,command,capture_output=True,text=True,timeout=35)
            self.run_dir.mkdir(exist_ok=True)
            (self.run_dir/'follow_connection.log').write_text(result.stdout+result.stderr)
            if result.returncode:raise ValueError((result.stderr+result.stdout)[-1000:] or 'YOLO 검출 준비 실패')
        finally:self.follow_preparing=False

    def set_tray_mode(self, mode, control, parameters):
        if mode not in self.tray_modes or control not in ('OFF','LEVEL','PREDICTIVE'):
            raise ValueError('트레이 제어 모드를 확인해 주세요.')
        if self.engine.active:raise ValueError('주행을 중지한 뒤 트레이 모드를 바꿔 주세요.')
        config=PredictionConfig.parse(parameters)
        if mode=='real':
            if not self.allow_real or self.robot is None:raise ValueError('실차와 ESP32를 먼저 연결해 주세요.')
            pose,_,reason=self.robot.read()
            if abs(pose.v)>.01 or abs(pose.w)>.03:raise ValueError('차체가 정지한 뒤 변경해 주세요.')
            if reason and control!='OFF':raise ValueError(reason)
            command='x' if control=='OFF' else 'h'
            check_command(command,self.tray_data)
            if control=='PREDICTIVE':check_command(prediction_command(0,0),self.tray_data)
            self.robot.send_tray(command)
        self.tray_modes[mode]=control;self.prediction_configs[mode]=config
        self.tray_configured[mode]=True
        self.predictors[mode].reset();self.tray_control_error=''

    def require_tray_ready(self):
        if self.tray_modes['real']=='OFF':
            if self.tray_configured['real'] and self.tray_data.get('connected') and self.tray_data.get('mode')!=0:
                raise ValueError('ESP32의 보정 꺼짐(PWM 유지) 상태 수신을 기다려 주세요.')
            return
        check_command('h',self.tray_data)
        target_mode=self.tray_data.get('predictive_mode',5) if self.tray_data.get('protocol')==2 else None
        if self.tray_data.get('mode') not in (4,target_mode):raise ValueError('ESP32의 수평 유지 상태 수신을 기다려 주세요.')
        if self.tray_modes['real']=='PREDICTIVE':check_command(prediction_command(0,0),self.tray_data)

    def end_prediction(self):
        if self.prediction_streamed and self.robot:
            try:self.robot.send_tray('h')
            except (ValueError,OSError) as exc:self.tray_control_error=str(exc)
        self.prediction_streamed=False
        for predictor in self.predictors.values():predictor.reset()

    def validate_request(self, request):
        host = request.headers.get('Host', '')
        origin = request.headers.get('Origin')
        if origin and origin != f'http://{host}':
            raise web.HTTPForbidden(text='허용되지 않은 Origin')
        if request.headers.get('X-Hongik-Control') != self.token:
            raise web.HTTPForbidden(text='로컬 제어 세션을 새로 열어 주세요.')

    async def connect(self, host=None, mapping=False):
        if not self.allow_real:
            raise ValueError('실차 기능은 start.sh --allow-real 로 열어 주세요.')
        if self.engine.active or self.connecting or self.follow_preparing:
            raise ValueError('진행 중인 시험 또는 연결 작업을 먼저 마쳐 주세요.')
        target = 'localhost' if local_robot() else host or self.robot_host
        if not isinstance(target,str) or not re.fullmatch(r'(?:seo@)?[A-Za-z0-9][A-Za-z0-9.-]{0,252}',target):
            raise ValueError('Jetson IP 또는 SJM.local 형식의 주소를 입력해 주세요.')
        if target != 'hongik-jetson' and not target.startswith('seo@'):
            target = 'seo@'+target
        if local_robot():target='localhost'
        if self.robot and not self.live_reason and target == self.robot_host and self.mapping_mode==mapping and not mapping:
            return
        self.connecting = True
        self.connection_message = 'Jetson 장치와 기존 세션을 확인하고 있습니다…'
        try:
            if self.robot:
                previous_robot, self.robot = self.robot, None
                await asyncio.to_thread(previous_robot.close)
            self.course_origin = self.robot_pose = None
            self.camera_info = {'available':False,'seq':0,'age':None}
            self.points = []
            self.tray_data = {'connected':False,'error':'ESP32 상태 대기'}
            self.lidar_map = None
            def prepare():
                prepare_args=(['bash',str(ROOT/'prepare_local.sh'),'--new-map' if mapping else '--attach']
                              if local_robot() else ['bash', str(ROOT / 'prepare_robot.sh'), target]+(['--mapping'] if mapping else []))
                return subprocess.run(prepare_args,
                                      capture_output=True, text=True, timeout=100)
            result = await asyncio.to_thread(prepare)
            self.run_dir.mkdir(exist_ok=True)
            (self.run_dir / 'connection.log').write_text(result.stdout + result.stderr)
            if result.returncode:
                raise ValueError((result.stderr + result.stdout)[-1200:] or 'Jetson 연결 실패')
            from ros_io import RosRobot
            self.robot = await asyncio.to_thread(RosRobot)
            self.robot_host = target
            self.mapping_mode = True if local_robot() else mapping
            self.connection_message = '센서 수신 대기 중 · 시험 버튼을 누르기 전까지 이동하지 않습니다.'
        except (subprocess.TimeoutExpired, ImportError, OSError) as exc:
            raise ValueError(f'연결 준비 실패: {exc}') from exc
        finally:
            self.connecting = False

    async def tray_remote(self, action, port=None):
        if not self.allow_real or self.robot is None:
            raise ValueError('Jetson 실차 연결을 먼저 준비해 주세요.')
        if self.engine.active: raise ValueError('차체 시험 중에는 USB 연결을 바꿀 수 없습니다.')
        script='/home/seo/d420_ws/scenario_lab/jetson/tray_session.sh'
        if action not in ('list','start'): raise ValueError('잘못된 ESP32 연결 요청')
        if action=='start':
            matches=[d for d in self.tray_ports if d.get('port')==port and not d.get('reserved')]
            if len(matches)!=1: raise ValueError('장치 목록에서 실제 ESP32를 선택해 주세요.')
        args=['/bin/bash',script,action]+([port] if action=='start' else [])
        command=robot_command(self.robot_host,args,['-n','-o','BatchMode=yes','-o','ConnectTimeout=5','-o','ConnectionAttempts=1',
                 '-o','HostKeyAlias=192.168.0.12','-o','StrictHostKeyChecking=yes'])
        result=await asyncio.to_thread(subprocess.run,command,capture_output=True,text=True,timeout=20)
        if result.returncode: raise ValueError((result.stderr+result.stdout)[-1000:])
        if action=='list':
            devices=json.loads(result.stdout)
            if not isinstance(devices,list): raise ValueError('ESP32 장치 목록을 읽지 못했습니다.')
            self.tray_ports=devices
        return self.tray_ports

    def tray_command(self, command, mode):
        if not isinstance(command,str) or command not in COMMANDS:raise ValueError('트레이 버튼 명령을 확인해 주세요.')
        if mode!='real': raise ValueError('실차 시험 모드에서만 서보 명령을 보낼 수 있습니다.')
        if not self.allow_real or self.robot is None or not hasattr(self.robot,'send_tray'):
            raise ValueError('실차와 ESP32를 먼저 연결해 주세요.')
        pose,_,reason=self.robot.read()
        if self.engine.active or abs(pose.v)>.01 or abs(pose.w)>.03:
            raise ValueError('트레이 설정은 차체를 정지한 뒤 진행해 주세요.')
        if reason and command not in ('x','s'): raise ValueError(reason)
        check_command(command,self.tray_data)
        nonce=self.robot.send_tray(command)
        if command=='h':self.tray_modes['real']='LEVEL'
        elif command in ('x','s','g','u','i','Q','W','E','R'):self.tray_modes['real']='OFF'
        if command in ('h','x','s','g','u','i','Q','W','E','R'):self.tray_configured['real']=True
        return nonce

    def relative_pose(self, pose):
        origin = self.course_origin
        if origin is None:
            return None
        c, s = math.cos(origin.yaw), math.sin(origin.yaw)
        dx, dy = pose.x-origin.x, pose.y-origin.y
        return {'x':c*dx+s*dy,'y':-s*dx+c*dy,
                'yaw':math.atan2(math.sin(pose.yaw-origin.yaw),math.cos(pose.yaw-origin.yaw)),
                'v':pose.v,'w':pose.w}

    def reset_course(self):
        if self.engine.active:
            raise ValueError('시험 중에는 출발선을 바꿀 수 없습니다.')
        if self.robot is None:
            raise ValueError('실차를 먼저 연결해 주세요.')
        pose, _, reason = self.robot.read()
        if reason:
            raise ValueError(reason)
        if abs(pose.v)>0.01 or abs(pose.w)>0.03:
            raise ValueError('완전히 정지한 상태에서 출발선을 설정해 주세요.')
        self.course_origin = Pose(pose.x,pose.y,pose.yaw)
        self.course_version += 1
        self.robot_pose = self.relative_pose(pose)

    def stop(self, reason):
        self.stop_epoch += 1
        rid=self.pending_local_route or (self.engine.route_id if isinstance(self.engine,RemoteDepthRoute) else None)
        if rid and self.robot:
            self.robot.send_local_route(dict(op='stop',route_id=rid,request_id=secrets.token_hex(16),created_unix=time.time()))
        if isinstance(self.engine,Navigation) and self.robot and getattr(self.robot,'nav',None):
            self.robot.nav.cancel()
        was_real = self.engine.mode == 'real' and self.engine.active
        self.engine.stop(reason)
        if was_real and self.robot:
            self.robot.publish(0, 0)
            self.stop_until = time.monotonic() + 0.8
        self.end_prediction()

    def status(self):
        state = self.engine.snapshot(time.monotonic())
        depth=self.robot.depth() if self.robot and hasattr(self.robot,'depth') else {'ready':False,'reason':'깊이 관측 대기'}
        state['depth_route']={k:v for k,v in depth.items() if k!='points'}
        state.update({'allow_real': self.allow_real, 'connecting': self.connecting,
                      'connected': self.robot is not None, 'ready': self.robot is not None and not self.live_reason,
                      'connection_message': self.connection_message, 'sensor_reason': self.live_reason,
                      'scan_points': self.points[::3], 'tray_connected':bool(self.tray_data.get('connected')),
                      'tray':self.tray_data, 'tray_ports':self.tray_ports,
                      'robot_pose':self.robot_pose, 'course_ready':self.course_origin is not None,
                      'camera':self.camera_info, 'lidar_map':self.lidar_map,
                      'course_version':self.course_version, 'robot_host':self.robot_host})
        state['drive_mode']='follow' if isinstance(self.engine,Follow) else 'teleop' if isinstance(self.engine,Teleop) else 'scenario'
        state['follow']={**self.follow_data,'preparing':self.follow_preparing}
        state['tray_controls']={mode:{'mode':self.tray_modes[mode], 'parameters':asdict(self.prediction_configs[mode]),
            'configured':self.tray_configured[mode],
            'roll_target_deg':self.predictors[mode].roll,'pitch_target_deg':self.predictors[mode].pitch,
            'source':'주행 명령·입력 코스 모델 / RealSense 깊이 미사용',
            'streaming':mode=='real' and self.prediction_streamed,
            'error':self.tray_control_error if mode=='real' else ''} for mode in self.tray_modes}
        return state

    def save_run(self):
        if not self.engine.history or self.last_saved_count == len(self.engine.history):
            return
        self.last_saved_count = len(self.engine.history)
        folder = self.run_dir
        folder.mkdir(exist_ok=True)
        name = f'{datetime.now():%Y%m%d_%H%M%S}_{self.engine.mode}_{self.engine.kind}_{time.time_ns()%1000000:06d}'
        payload = {'summary': self.engine.snapshot(time.monotonic()), 'samples': self.engine.history,
                   'notes': 'Preview is kinematic. Tray values come only from valid ESP32 reports, nominally 2Hz sample-and-hold; PWM is a command. No measured spill volume.'}
        (folder / (name + '.json')).write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    async def loop(self):
        try:
            index = 0
            while True:
                now = time.monotonic()
                dt = min(0.1, now - self.last_loop)
                self.last_loop = now
                was_active = self.engine.active
                previous_sample_count = len(self.engine.history)
                if self.robot:
                    pose, points, reason = self.robot.read(require_lidar=False) if isinstance(self.engine,(DepthRoute,RemoteDepthRoute)) else self.robot.read()
                    if hasattr(self.robot,'local_route'):
                        local=self.robot.local_route()
                        if (not self.engine.active and not self.pending_local_route and local.get('transport_age_ms',99999)<800
                                and local.get('engine',{} ) and local['engine'].get('active')):
                            self.engine=RemoteDepthRoute(local['route_id'])
                        if isinstance(self.engine,RemoteDepthRoute):self.engine.update(local,now)
                    self.live_reason = reason
                    self.points = points
                    if hasattr(self.robot,'tray'): self.tray_data=self.robot.tray()
                    if hasattr(self.robot,'follow'):self.follow_data=self.robot.follow()
                    if isinstance(self.engine,Navigation) and self.engine.active:
                        self.engine.observe(self.robot.navigation().snapshot())
                    if isinstance(self.engine,Follow):self.engine.observe(self.follow_data,time.time())
                    if isinstance(self.engine,DepthRoute):self.engine.observe(self.robot.depth(),time.time())
                    if self.course_origin is None and not reason:
                        self.course_origin = Pose(pose.x,pose.y,pose.yaw)
                        self.course_version += 1
                    self.robot_pose = self.relative_pose(pose)
                    if hasattr(self.robot, 'media'):
                        self.camera_info, grid = self.robot.media()
                        if grid and self.course_origin:
                            origin=self.course_origin
                            c,s=math.cos(origin.yaw),math.sin(origin.yaw)
                            dx,dy=grid['x']-origin.x,grid['y']-origin.y
                            self.lidar_map={**grid,'x':c*dx+s*dy-0.135,'y':-s*dx+c*dy,
                                            'yaw':grid['yaw']-origin.yaw}
                        else:
                            self.lidar_map=None
                if self.engine.mode == 'preview':
                    v, w = self.engine.command
                    alpha = min(1.0, dt / 0.12)
                    self.sim.v += (v - self.sim.v) * alpha
                    self.sim.w += (w - self.sim.w) * alpha
                    self.sim.yaw += self.sim.w * dt
                    self.sim.x += self.sim.v * math.cos(self.sim.yaw) * dt
                    self.sim.y += self.sim.v * math.sin(self.sim.yaw) * dt
                    self.engine.step(now, self.sim)
                elif self.robot:
                    if isinstance(self.engine,RemoteDepthRoute):
                        pass  # Jetson owns control, sensor freshness and stop acknowledgement.
                    elif self.engine.active:
                        if reason:
                            if isinstance(self.engine,Teleop) and reason.startswith('최신 '):
                                self.engine.hold(reason)
                                self.engine.pose = Pose(**asdict(pose))
                                self.engine.last_tick = now
                            else:
                                self.stop(reason)
                        elif now - self.heartbeat > 0.8:
                            self.stop('브라우저 연결이 끊겼습니다.')
                        elif dt >= 0.095:
                            if isinstance(self.engine,DepthRoute):
                                self.engine.pause('제어 주기가 지연됐습니다.',now)
                                self.engine.last_tick=now
                                self.engine.sample(now,pose)
                            elif isinstance(self.engine,Teleop):
                                self.engine.hold('제어 주기가 지연됐습니다.')
                                self.engine.pose = Pose(**asdict(pose))
                                self.engine.last_tick = now
                            else:
                                self.stop('제어 주기가 지연됐습니다.')
                        else:
                            try:self.require_tray_ready()
                            except ValueError as exc:self.stop('트레이 준비 상태 변경: '+str(exc))
                            if self.engine.active:
                                # Manual/scenario retain the operator's collision-stop setting.
                                # New close/reverse following checks its own swept clearance.
                                self.engine.step(now, pose)
                                if isinstance(self.engine,Follow) and self.engine.active:
                                    check=(self.robot.follow_motion_reason if hasattr(self.robot,'follow_motion_reason')
                                           else lambda v,w:'근접 추종 관측을 확인할 수 없습니다.')
                                    requested=self.engine.command
                                    checked,guard=limit_command(requested,check)
                                    if guard:
                                        self.engine.command=checked
                                        self.engine.state='follow_hold' if checked==(0.,0.) else 'follow'
                                        self.engine.message=guard
                                        if self.engine.history:
                                            self.engine.history[-1].update(cmd_v=checked[0],cmd_w=checked[1],phase=self.engine.state,
                                                                          requested_v=requested[0],requested_w=requested[1],motion_guard=guard)
                        self.robot.publish(*self.engine.command)
                    elif now < self.stop_until:
                        self.robot.publish(0, 0)
                control_mode=self.engine.mode
                if self.tray_modes[control_mode]=='PREDICTIVE':
                    roll,pitch=self.predictors[control_mode].step(self.engine,now,dt,self.prediction_configs[control_mode])
                    if self.engine.active and control_mode=='real' and self.robot:
                        try:
                            self.robot.send_tray(prediction_command(roll,pitch));self.prediction_streamed=True
                        except (ValueError,OSError) as exc:
                            self.tray_control_error=str(exc);self.stop('예측 목표 전송 실패: '+str(exc))
                else:self.predictors[control_mode].reset()
                if len(self.engine.history)>previous_sample_count and self.engine.mode=='real':
                    row=self.engine.history[-1]
                    tray=self.tray_data
                    valid=bool(tray.get('connected') and tray.get('imu_fresh'))
                    row.update(tray_valid=valid,tray_roll_deg=tray.get('roll_deg') if valid else None,
                               tray_pitch_deg=tray.get('pitch_deg') if valid else None,
                               tray_mode=tray.get('mode'),tray_age_s=tray.get('status_age_s'),
                               tray_zero_saved=tray.get('zero_saved'))
                if len(self.engine.history)>previous_sample_count:
                    control=self.predictors[control_mode]
                    self.engine.history[-1].update(tray_control=self.tray_modes[control_mode],
                        tray_target_roll_deg=control.roll,tray_target_pitch_deg=control.pitch,
                        prediction_source='COMMAND_COURSE_MODEL_NOT_DEPTH')
                if was_active and not self.engine.active:
                    if isinstance(self.engine,Navigation) and self.robot and getattr(self.robot,'nav',None):
                        self.robot.nav.cancel()
                    if self.engine.mode == 'real':
                        self.stop_until = now + 0.8
                    self.end_prediction()
                    self.save_run()
                if index % 2 == 0:
                    state = json.dumps(self.status(), ensure_ascii=False)
                    for socket in list(self.sockets):
                        try:
                            await asyncio.wait_for(socket.send_str(state), timeout=0.15)
                        except Exception:
                            self.sockets.discard(socket)
                index += 1
                await asyncio.sleep(max(0.001, 0.05 - (time.monotonic() - now)))
        except asyncio.CancelledError:
            pass
        finally:
            self.stop('서버 종료')
            if self.robot:
                await asyncio.to_thread(self.robot.close)


def create_app(allow_real=False):
    lab = Lab(allow_real)

    @web.middleware
    async def local_host_only(request, handler):
        if request.host.split(':', 1)[0].lower() not in ('127.0.0.1', 'localhost'):
            raise web.HTTPForbidden(text='로컬 호스트 주소로 열어 주세요.')
        return await handler(request)

    app = web.Application(client_max_size=8192, middlewares=[local_host_only])
    app['lab'] = lab
    from map_workspace import install as install_mapping
    install_mapping(app,lab,ROOT)
    from rviz_view import install as install_rviz
    install_rviz(app,lab)

    async def index(request):
        response = web.FileResponse(ROOT / 'web/index.html')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        return response

    async def session(request):
        return web.json_response({'token': lab.token, 'allow_real': allow_real, 'host':lab.robot_host}, headers={'Cache-Control': 'no-store'})

    async def status(request):
        return web.json_response(lab.status())

    async def action(request):
        lab.validate_request(request)
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError('요청 형식을 확인해 주세요.')
            kind = request.match_info['action']
            if app['mapping'].busy and kind not in ('stop','heartbeat','teleop-input'):
                raise ValueError('지도 준비 작업을 먼저 마쳐 주세요.')
            if kind == 'connect':
                await lab.connect(data.get('host'))
            elif kind == 'tray-ports':
                await lab.tray_remote('list')
            elif kind == 'tray-connect':
                await lab.tray_remote('start',data.get('port'))
            elif kind == 'tray-command':
                nonce=lab.tray_command(data.get('command'),data.get('mode'))
                return web.json_response({'ok':True,'request_id':nonce,'state':lab.status()})
            elif kind == 'tray-mode':
                lab.set_tray_mode(data.get('mode'),data.get('control'),data.get('parameters',{}))
            elif kind == 'follow-prepare':
                await lab.prepare_follow()
            elif kind == 'teleop-input':
                if not isinstance(lab.engine,Teleop) or data.get('owner')!=lab.owner:
                    raise ValueError('이 창에서 수동 조종을 먼저 켜 주세요.')
                lab.engine.input(data.get('forward'),data.get('turn'),data.get('speed'),data.get('yaw_speed'),
                                 data.get('sequence'),data.get('control_id'),time.monotonic())
                if lab.engine.mode=='real' and lab.robot and lab.engine.axes==(0,0):
                    lab.robot.publish(0,0)
            elif kind in ('start','teleop-start','follow-start','depth-route-start'):
                if lab.engine.active or lab.follow_preparing or lab.connecting:raise ValueError('현재 조종/준비 작업을 먼저 마쳐 주세요.')
                mode = data.get('mode', 'preview')
                owner = data.get('owner')
                if not isinstance(owner, str) or not 8 <= len(owner) <= 80:
                    raise ValueError('브라우저 제어 세션을 확인해 주세요.')
                config = Config.parse(data.get('config', {}),max_speed=Teleop.MAX_SPEED if kind=='teleop-start' else DepthRoute.MAX_SPEED if kind=='depth-route-start' else .30)
                if kind=='depth-route-start':
                    if mode!='real':raise ValueError('깊이 경로는 실차 모드 전용입니다.')
                    if lab.tray_modes['real']=='PREDICTIVE':raise ValueError('깊이 경로는 트레이 실행 안함/수평 유지로 사용해 주세요.')
                if kind=='follow-start':
                    if mode!='real':raise ValueError('사람 추종은 실제 카메라 연결 후 실차 모드에서 사용합니다.')
                    if lab.tray_modes['real']=='PREDICTIVE':raise ValueError('사람 추종은 트레이 실행 안함 또는 수평 유지로 사용해 주세요.')
                    config.speed=min(config.speed,.12)
                pose = Pose()
                if mode == 'real':
                    if not allow_real or lab.robot is None:
                        raise ValueError('실차 연결을 먼저 준비해 주세요.')
                    pose, points, reason = lab.robot.read(require_lidar=False) if kind=='depth-route-start' else lab.robot.read()
                    if reason:
                        raise ValueError(reason)
                    if lab.course_origin is None:
                        lab.course_origin = Pose(pose.x,pose.y,pose.yaw)
                        lab.course_version += 1
                    if kind=='start':
                        relative=lab.relative_pose(pose)
                        if relative is None:
                            raise ValueError('출발선 위치 수신을 기다려 주세요.')
                        if math.hypot(relative['x'],relative['y'])>0.05 or abs(relative['yaw'])>math.radians(5):
                            raise ValueError('로봇을 출발선에 놓거나 현재 위치를 새 출발선으로 설정해 주세요.')
                    if kind=='start' and data.get('kind') == 'bump' and data.get('bump_measured') is not True:
                        raise ValueError('실제 턱 높이·거리·폭을 입력해 주세요.')
                    lab.require_tray_ready()
                elif mode != 'preview':
                    raise ValueError('실행 모드를 확인해 주세요.')
                if not lab.engine.active:
                    lab.save_run()
                if kind=='depth-route-start':
                    local=lab.robot.local_route()
                    if local.get('transport_age_ms',99999)>800:raise ValueError('Jetson 내부 경로 제어 연결을 기다려 주세요.')
                    route_id=secrets.token_hex(16);request_id=secrets.token_hex(16);epoch=lab.stop_epoch
                    goal=dict(op='start',route_id=route_id,request_id=request_id,created_unix=time.time(),
                              bridge_session=local.get('bridge_session'),first=data.get('first'),second=data.get('second'),speed=config.speed,trial=data.get('trial',False),brake=data.get('brake',True))
                    lab.pending_local_route=route_id
                    try:
                        deadline=time.monotonic()+1.5
                        while time.monotonic()<deadline:
                            if lab.stop_epoch!=epoch:raise ValueError('주행 시작이 취소됐습니다.')
                            lab.robot.send_local_route(goal)
                            await asyncio.sleep(.1)
                            local=lab.robot.local_route();reply=local.get('reply',{})
                            if reply.get('request_id')==request_id:
                                if not reply.get('accepted'):raise ValueError(reply.get('reason','Jetson 시작 거절'))
                                if lab.stop_epoch!=epoch:raise ValueError('주행 시작이 취소됐습니다.')
                                route=RemoteDepthRoute(route_id);route.update(local,time.monotonic());lab.engine=route
                                break
                        else:raise ValueError('Jetson 시작 확인 시간 초과 · 취소 요청 전송 중')
                    except BaseException:
                        lab.robot.send_local_route(dict(op='stop',route_id=route_id,request_id=secrets.token_hex(16),created_unix=time.time()))
                        raise
                    finally:lab.pending_local_route=None
                elif kind=='follow-start':
                    observation=lab.robot.follow() if hasattr(lab.robot,'follow') else {}
                    follower=Follow()
                    follower.arm(config,time.monotonic(),pose,observation,time.time(),data.get('target_id'),data.get('distance',.3))
                    lab.engine=follower
                elif kind=='teleop-start':
                    lab.engine=Teleop();lab.engine.arm(mode,config,time.monotonic(),pose)
                else:
                    lab.engine=Engine();lab.engine.start(data.get('kind'), mode, config, time.monotonic(), pose)
                lab.sim = Pose()
                lab.owner, lab.heartbeat = owner, time.monotonic()
                lab.last_saved_count = 0
            elif kind == 'heartbeat':
                if data.get('owner') == lab.owner:
                    lab.heartbeat = time.monotonic()
            elif kind == 'reset-course':
                lab.reset_course()
            elif kind == 'stop':
                lab.stop('사용자가 중지했습니다.')
                lab.save_run()
            else:
                raise ValueError('지원하지 않는 명령입니다.')
            return web.json_response({'ok': True, 'state': lab.status()})
        except (ValueError, TypeError, json.JSONDecodeError, subprocess.TimeoutExpired, OSError) as exc:
            return web.json_response({'ok': False, 'error': str(exc)}, status=400)

    async def socket(request):
        origin = request.headers.get('Origin')
        if origin and origin != f'http://{request.host}':
            raise web.HTTPForbidden()
        ws = web.WebSocketResponse(heartbeat=5)
        await ws.prepare(request)
        lab.sockets.add(ws)
        try:
            await ws.send_json(lab.status())
            async for _ in ws:
                pass
        finally:
            lab.sockets.discard(ws)
        return ws

    async def export(request):
        output = io.StringIO()
        fields = ['t', 'x', 'y', 'yaw', 'v', 'w', 'cmd_v', 'cmd_w', 'phase',
                  'tray_valid','tray_roll_deg','tray_pitch_deg','tray_mode','tray_age_s','tray_zero_saved',
                  'tray_control','tray_target_roll_deg','tray_target_pitch_deg','prediction_source',
                  'requested_v','requested_w','motion_guard']
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(lab.engine.history)
        return web.Response(text=output.getvalue(), content_type='text/csv',
                            headers={'Content-Disposition': f'attachment; filename="hongik_{lab.engine.mode}_{lab.engine.kind}.csv"'})

    async def camera_frame(request):
        if lab.robot is None or not hasattr(lab.robot, 'camera_frame'):
            return web.Response(status=204, headers={'Cache-Control':'no-store'})
        jpeg = lab.robot.camera_frame()
        if jpeg is None:
            return web.Response(status=204, headers={'Cache-Control':'no-store'})
        return web.Response(body=jpeg, content_type='image/jpeg', headers={'Cache-Control':'no-store'})

    async def firmware(request):
        response=web.FileResponse(ROOT/'firmware/esp32_app_control/esp32_app_control.ino')
        response.headers['Content-Disposition']='attachment; filename="esp32_app_control.ino"'
        response.headers['Cache-Control']='no-store'
        return response

    async def startup(app):
        app['ticker'] = asyncio.create_task(lab.loop())
        if local_robot() and allow_real:
            lab.connecting=True
            try:
                from ros_io import RosRobot
                lab.robot=await asyncio.to_thread(RosRobot)
                lab.mapping_mode=True
                lab.connection_message='Jetson 로컬 센서 연결 · 조종을 켜기 전까지 정지'
            except (ImportError,OSError,RuntimeError) as exc:
                lab.connection_message='로컬 센서 연결 실패: '+str(exc)
            finally:lab.connecting=False

    async def shutdown(app):
        app['ticker'].cancel()
        await app['ticker']
        for ws in list(lab.sockets):
            await ws.close()

    app.router.add_get('/', index)
    app.router.add_get('/api/session', session)
    app.router.add_get('/api/status', status)
    app.router.add_get('/api/export.csv', export)
    app.router.add_get('/api/camera.jpg', camera_frame)
    app.router.add_get('/api/firmware.ino', firmware)
    app.router.add_post('/api/{action}', action)
    app.router.add_get('/ws', socket)
    app.router.add_static('/static/', ROOT / 'web', show_index=False)
    app.on_startup.append(startup)
    app.on_cleanup.append(shutdown)
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--allow-real', action='store_true', help='Enable operator-initiated Jetson preparation.')
    args = parser.parse_args()
    web.run_app(create_app(args.allow_real), host='127.0.0.1', port=args.port, print=print)
