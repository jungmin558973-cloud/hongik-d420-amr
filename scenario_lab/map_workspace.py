from robot_runtime import command as robot_command
"""Mapping and named goals in the existing local HONGIK console."""
import asyncio
import json
import shlex
import subprocess
import time
from aiohttp import web
from navigation import Navigation,Waypoints
from engine import Config
from operator_control import Teleop


class MapWorkspace:
    def __init__(self,lab,root):
        self.lab=lab;self.root=root
        self.store=Waypoints(root/'runs/navigation/maps.json')
        self.busy=False;self.message='수동으로 지도를 만든 뒤 지도 저장을 누르세요.'
        self.path=[]

    def snapshot(self):
        grid=None;reason='실차 연결을 먼저 준비해 주세요.'
        if self.lab.robot:
            try:
                grid=self.lab.robot.mapping();reason=''
                if self.store.current is None:
                    item=next((m for m in reversed(self.store.maps) if m.get('session')==grid['session']),None)
                    if item:self.store.current=item['id']
                if self.store.active() and self.store.active().get('session')!=grid['session']:
                    self.store.current=None;self.path=[]
            except Exception as exc:reason=str(exc)
        engine=self.lab.engine
        nav=getattr(self.lab.robot,'nav',None)
        return {'map':grid,'reason':reason,'sensor_reason':self.lab.live_reason,
                'ready':grid is not None and not self.lab.live_reason,
                'manual_ready':self.lab.robot is not None and not self.lab.live_reason,
                'busy':self.busy,'message':self.message,
                'active':engine.active,'kind':engine.kind,'state':engine.state,'run_message':engine.message,
                'command':engine.command,'maps':self.store.maps,'current_map':self.store.current,'path':self.path,
                'nav':nav.snapshot() if nav else {'ready':False},'mapping_mode':self.lab.mapping_mode}

    async def ssh(self,args,timeout=60):
        command=robot_command(self.lab.robot_host,args)
        response=await asyncio.to_thread(subprocess.run,command,capture_output=True,text=True,timeout=timeout)
        if response.returncode:raise ValueError((response.stderr+response.stdout)[-1200:] or 'Jetson 작업 실패')
        return response.stdout

    def stationary(self):
        if self.lab.engine.active:raise ValueError('조종·이동을 먼저 정지해 주세요.')
        if self.lab.robot is None:raise ValueError('실차 연결을 먼저 준비해 주세요.')
        pose,_,reason=self.lab.robot.read()
        if reason:raise ValueError(reason)
        if abs(pose.v)>.01 or abs(pose.w)>.03:raise ValueError('차체가 완전히 정지한 뒤 진행해 주세요.')
        self.lab.robot.mapping()

    def live_item(self,mapping,name='작성 중인 지도'):
        item=self.store.active()
        if item is None or item.get('session')!=mapping['session']:
            item=self.store.bind(name,None)
            item['session']=mapping['session'];self.store.write()
        return item

    async def action(self,kind,data):
        if self.busy:raise ValueError('지도 작업이 진행 중입니다.')
        if kind=='attach':
            if not self.lab.allow_real:raise ValueError('실차 기능이 비활성화돼 있습니다.')
            if self.lab.engine.active or self.lab.connecting:raise ValueError('현재 조종을 먼저 종료해 주세요.')
            self.busy=True
            try:
                output=await self.ssh(['systemctl','--user','show','hongik-scenarios.service','--property=Environment','--property=ActiveState'])
                if 'D420_MAPPING_MODE=1' not in output or 'ActiveState=active' not in output:
                    raise ValueError('실행 중인 지도 세션이 없습니다. 새 지도 작성 연결을 사용해 주세요.')
                if self.lab.robot is None:
                    from ros_io import RosRobot
                    self.lab.robot=await asyncio.to_thread(RosRobot)
                self.lab.mapping_mode=True
                self.message='기존 지도를 유지하고 센서에 다시 연결했습니다.'
            finally:self.busy=False
        elif kind=='depth-prepare':
            if not self.lab.allow_real or self.lab.robot is None:raise ValueError('실차 연결을 먼저 준비해 주세요.')
            if self.lab.engine.active:raise ValueError('주행을 먼저 정지해 주세요.')
            self.busy=True
            try:
                await self.ssh(['bash','/home/seo/d420_ws/scenario_lab/jetson/depth_prepare.sh'])
                self.message='깊이 관측 연결 요청 완료 · 관측 상태를 확인해 주세요.'
            finally:self.busy=False
        elif kind=='connect':
            self.busy=True
            try:
                await self.lab.connect(data.get('host'),mapping=True)
                self.store.current=None;self.path=[]
                self.message='새 SLAM 지도 수신 대기 · 수동 조종으로 주변을 천천히 이동하세요.'
            finally:self.busy=False
        elif kind=='save-map':
            self.stationary();self.busy=True
            try:
                output=await self.ssh(['bash','/home/seo/d420_ws/experiments/manual_mapping_20261024/save.sh'])
                saved=None;decoder=json.JSONDecoder()
                for index,char in enumerate(output):
                    if char!='{':continue
                    try:
                        obj,_=decoder.raw_decode(output[index:])
                        if isinstance(obj,dict) and 'saved' in obj:saved=obj;break
                    except ValueError:pass
                if not saved:raise ValueError('지도 저장 결과를 확인하지 못했습니다.')
                mapping=self.lab.robot.mapping()
                item=self.live_item(mapping,data.get('name','실험실 지도'))
                item.update(name=str(data.get('name','실험실 지도')).strip()[:60] or '실험실 지도',remote_path=saved['saved'])
                self.store.write()
                self.message='지도·포즈 그래프 저장 완료. 지도에서 빈 공간을 클릭해 지점을 저장하세요.'
            finally:self.busy=False
        elif kind=='save-point':
            self.stationary()
            mapping=self.lab.robot.mapping()
            point=self.lab.robot.check_map_point(data.get('point'))
            self.live_item(mapping,data.get('map_name','작성 중인 지도'))
            self.store.add(data.get('name',''),point)
            self.message='현재 지도에 지점을 기록했습니다. 지도 작성 후 지도를 저장해 주세요.'
        elif kind=='mark-current':
            if not self.lab.mapping_mode or self.lab.robot is None:raise ValueError('지도 작성 연결을 먼저 준비해 주세요.')
            if self.lab.engine.active and not isinstance(self.lab.engine,Teleop):
                raise ValueError('자율 이동을 먼저 정지해 주세요.')
            pose,_,reason=self.lab.robot.read()
            if reason:raise ValueError(reason)
            if (abs(pose.v)>.01 or abs(pose.w)>.03 or
                    any(abs(v)>.001 for v in self.lab.engine.command)):
                raise ValueError('방향 키를 놓고 차체가 완전히 멈춘 뒤 기록해 주세요.')
            role=data.get('role')
            labels={'start':'출발점','turn':'회전 지점','finish':'도착점'}
            if role not in labels:raise ValueError('출발점·회전 지점·도착점 중 선택해 주세요.')
            mapping=self.lab.robot.mapping()
            item=self.live_item(mapping,data.get('map_name','작성 중인 지도'))
            existing=[p for p in item['points'] if p.get('role')==role]
            name=labels[role] if not existing else labels[role]+' '+str(len(existing)+1)
            point=self.store.add(name,mapping['robot'])
            point['role']=role;self.store.write()
            self.message=name+' 기록 완료 · 현재 위치와 방향을 저장했습니다.'
        elif kind=='delete-point':
            self.stationary();item=self.store.active();point=self.store.get(data.get('id'))
            item['points'].remove(point);self.store.write();self.path=[]
        elif kind=='prepare-nav':
            self.stationary()
            if not self.store.active() or not self.store.active().get('remote_path'):raise ValueError('현재 지도를 먼저 저장해 주세요.')
            if not self.lab.mapping_mode:raise ValueError('지도 작성 연결을 먼저 준비해 주세요.')
            self.busy=True
            try:
                await self.ssh(['bash','/home/seo/d420_ws/scenario_lab/jetson/navigation/start.sh'])
                nav=self.lab.robot.navigation();end=time.monotonic()+35
                while not nav.ready():
                    if time.monotonic()>end:raise ValueError('Nav2 준비 시간이 초과됐습니다.')
                    await asyncio.sleep(.2)
                self.message='지점 이동 준비 완료. 지점 선택 → 경로 확인 → 이동 시작 순서입니다.'
            finally:self.busy=False
        elif kind in ('plan','go'):
            if not self.lab.mapping_mode:raise ValueError('지도 작성 연결을 먼저 준비해 주세요.')
            self.stationary();mapping=self.lab.robot.mapping();item=self.store.active()
            if item is None or not item.get('remote_path') or item.get('session')!=mapping['session']:
                raise ValueError('현재 지도에 저장한 지점을 선택해 주세요.')
            point=self.store.get(data.get('id'));self.lab.robot.check_map_point(point)
            owner=data.get('owner')
            if kind=='go' and (not isinstance(owner,str) or not 8<=len(owner)<=80):
                raise ValueError('이동 제어 세션을 확인해 주세요.')
            self.busy=True
            try:
                stop_epoch=self.lab.stop_epoch
                nav=self.lab.robot.navigation();self.path=await nav.plan(point)
                if kind=='go':
                    await self.lab.prepare_recording()
                    speed=Config.parse({'speed':data.get('speed',.40)},max_speed=1.5).speed
                    # Recheck after planning: no motion if sensors/position became stale.
                    if self.lab.stop_epoch!=stop_epoch:raise ValueError('정지 요청으로 이동을 취소했습니다.')
                    self.stationary();self.lab.require_tray_ready()
                    await nav.set_speed(speed)
                    self.stationary()
                    if self.lab.stop_epoch!=stop_epoch:raise ValueError('정지 요청으로 이동을 취소했습니다.')
                    goal_id=await nav.start(point)
                    try:
                        if (self.lab.engine.active or self.lab.stop_epoch!=stop_epoch or
                                self.lab.robot.mapping()['session']!=mapping['session']):
                            raise ValueError('제어 상태가 바뀌어 이동을 취소했습니다.')
                        pose,_,reason=self.lab.robot.read()
                        if reason:raise ValueError(reason)
                        controller=Navigation();controller.arm(time.monotonic(),pose,goal_id,speed)
                    except Exception:nav.cancel();raise
                    self.lab.save_run();self.lab.engine=controller
                    self.lab.owner=owner;self.lab.heartbeat=time.monotonic()
                    self.lab.last_saved_count=0
                    self.message=point['name']+' 지점으로 이동 중'
                else:self.message='차체 크기와 장애물을 고려한 경로 확인 완료'
            finally:self.busy=False
        else:raise ValueError('지원하지 않는 지도 작업입니다.')


def install(app,lab,root):
    workspace=MapWorkspace(lab,root)
    async def page(request):
        response=web.FileResponse(root/'web/navigation.html')
        response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        return response
    async def status(request):return web.json_response(workspace.snapshot())
    async def action(request):
        lab.validate_request(request)
        try:
            data=await request.json()
            if not isinstance(data,dict):raise ValueError('요청 형식을 확인해 주세요.')
            await workspace.action(request.match_info['kind'],data)
            return web.json_response({'ok':True,'state':workspace.snapshot()})
        except (ValueError,RuntimeError,OSError,subprocess.TimeoutExpired) as exc:
            return web.json_response({'ok':False,'error':str(exc)},status=400)
    app.router.add_get('/navigation',page)
    app.router.add_get('/api/navigation',status)
    app.router.add_post('/api/navigation/{kind}',action)
    app['mapping']=workspace
