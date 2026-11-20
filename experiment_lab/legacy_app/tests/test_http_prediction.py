import sys,time,tempfile,unittest,importlib.util
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
AVAILABLE=bool(importlib.util.find_spec('aiohttp') and importlib.util.find_spec('PIL'))

@unittest.skipUnless(AVAILABLE,'HTTP tests require aiohttp and Pillow')
class HttpPredictionTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  from aiohttp.test_utils import TestClient,TestServer
  import server
  from engine import Pose
  from jetson.local_route_host import RouteHost
  from test_route_prediction import ready_status,observation
  class Robot:
   def __init__(self):self.host=RouteHost();self.sent=[];self.tray_sent=[];self.proto=2;self.mode=4;self.roll=0.
   def read(self,**kwargs):return Pose(),[],''
   def tray(self):return {**ready_status(), 'protocol':self.proto,'mode':self.mode,'roll_deg':self.roll,'connected':True,'status_age_s':0.,'stamp_unix':time.time()}
   def depth(self):return observation(time.time())
   def local_route(self):return {**self.host.state(time.monotonic(),time.time()),'transport_age_ms':0}
   def send_local_route(self,d):
    self.sent.append(d);self.host.observation(observation(time.time()),time.monotonic());self.host.request(d,Pose(),True,time.monotonic(),time.time())
   def publish(self,v,w):
    if v or w:raise AssertionError('UI must never drive local route')
   def send_tray(self,c):
    self.tray_sent.append(c)
    if c=='h':self.mode=4;return
    if c=='x':self.mode=0;return
    if c=='z':return
    raise AssertionError('UI must not stream route targets')
   def close(self):pass
  self.temp=tempfile.TemporaryDirectory();self.local=patch.object(server,'local_robot',lambda:False);self.local.start()
  self.app=server.create_app(True);self.lab=self.app['lab'];self.lab.run_dir=Path(self.temp.name)
  self.robot=Robot();self.lab.robot=self.robot;self.lab.tray_data=self.robot.tray();self.lab.live_reason=''
  self.lab.tray_modes['real']='PREDICTIVE';self.lab.tray_configured['real']=True
  self.client=TestClient(TestServer(self.app));await self.client.start_server()
  self.headers={'X-Hongik-Control':self.lab.token}
 async def asyncTearDown(self):
  await self.client.close();self.local.stop();self.temp.cleanup()
 def body(self):return dict(mode='real',owner='test-owner',first=3.2,second=2.2,trial=False,brake=True,tray_level=True,tray_predictive=True,tray_prediction={'lead':.2,'angle_limit':2.},config={'speed':.12})
 async def test_custom_brake_setup_without_esp32_starts_chassis_only(self):
  self.robot.tray=lambda:dict(connected=False,error='no USB');self.lab.tray_data=self.robot.tray()
  data=self.body();data.update(config={'speed':.3},experiment_options={'test':'brake_line','tray':'CHASSIS','smooth_pct':0})
  res=await self.client.post('/api/depth-route-start',json=data,headers=self.headers);reply=await res.json()
  self.assertTrue(reply['ok'],reply);self.assertTrue(self.robot.host.engine.brake_only);self.assertEqual(self.robot.tray_sent,[])
  res=await self.client.get('/experiment-setup');self.assertIn('160cm',await res.text())
 async def test_case_endpoint_and_combined_40cm_s_request(self):
  res=await self.client.get('/api/experiment-cases');plan=await res.json()
  self.assertEqual(plan['speeds_cm_s'],[20,30,40]);self.assertEqual(len(plan['cases']),5)
  data=self.body();data.update(experiment_case='combined',repeat=3,turn_extension=.3,config={'speed':.4})
  res=await self.client.post('/api/depth-route-start',json=data,headers=self.headers);reply=await res.json()
  self.assertTrue(reply['ok'],reply)
  e=self.robot.host.engine;self.assertTrue(e.smooth_enabled);self.assertEqual(e.experiment_case['id'],'combined');self.assertEqual(e.lengths,(3.5,2.2));self.assertEqual(e.config.speed,.4)
 async def test_scurve_only_selects_fixed_pwm_and_disables_prediction(self):
  data=self.body();data.update(experiment_case='scurve',config={'speed':.3})
  res=await self.client.post('/api/depth-route-start',json=data,headers=self.headers);reply=await res.json()
  self.assertTrue(reply['ok'],reply);self.assertEqual(self.robot.tray_sent,['x','z'])
  self.assertTrue(self.robot.host.engine.smooth_enabled);self.assertIsNone(self.robot.host.predictor);self.assertTrue(self.robot.host.tray_fixed)
 async def test_web_server_disconnect_does_not_cancel_accepted_local_mission(self):
  res=await self.client.post('/api/depth-route-start',json=self.body(),headers=self.headers)
  self.assertTrue((await res.json())['ok']);await self.client.close()
  self.assertTrue(self.robot.host.engine.active)
  self.assertFalse(any(x['op']=='stop' for x in self.robot.sent))
 async def test_experiment_start_accumulates_sqlite_and_exports(self):
  import asyncio,csv,io
  res=await self.client.post('/api/start',json=dict(mode='preview',owner='test-owner',kind='brake'),headers=self.headers)
  self.assertTrue((await res.json())['ok']);await asyncio.sleep(.2)
  await self.client.post('/api/stop',json={},headers=self.headers);await asyncio.sleep(.35)
  res=await self.client.get('/api/experiments');records=(await res.json())['experiments']
  self.assertEqual(len(records),1);self.assertGreater(records[0]['sample_count'],0);self.assertEqual(records[0]['status'],'stopped')
  rid=records[0]['run_id'];res=await self.client.get('/api/experiments/'+rid+'.csv')
  rows=list(csv.DictReader(io.StringIO((await res.text()).lstrip('\ufeff'))))
  self.assertEqual(len(rows),records[0]['sample_count']);self.assertIn('estimated_ax_m_s2',rows[0]);self.assertIn('esp_target_pitch_deg',rows[0])
  res=await self.client.get('/api/experiments/'+rid+'.json');self.assertEqual(len((await res.json())['samples']),len(rows))
  res=await self.client.get('/api/experiments/unknown-run-123.json');self.assertEqual(res.status,404)
  res=await self.client.get('/experiments');self.assertIn('실험 기록',await res.text())
 async def test_http_start_forwards_local_prediction_and_ui_does_not_publish(self):
  import asyncio
  response=await self.client.post('/api/depth-route-start',json=self.body(),headers=self.headers)
  data=await response.json();self.assertTrue(data['ok'],data)
  goal=next(x for x in self.robot.sent if x['op']=='start');self.assertTrue(goal['tray_predictive']);self.assertEqual(goal['tray_prediction']['angle_limit'],2)
  with patch.object(self.lab.predictors['real'],'step',side_effect=AssertionError('UI predictor must be bypassed')) as predictor:
   await asyncio.sleep(.15);predictor.assert_not_called()
  self.assertEqual(self.robot.tray_sent,[])
 async def test_invalid_configuration_or_old_protocol_rejects_before_start(self):
  d=self.body();d['tray_prediction']['lead']=0
  res=await self.client.post('/api/depth-route-start',json=d,headers=self.headers);self.assertFalse((await res.json())['ok']);self.assertEqual(self.robot.sent,[])
  self.robot.proto=1;self.lab.tray_data=self.robot.tray()
  res=await self.client.post('/api/depth-route-start',json=self.body(),headers=self.headers);self.assertFalse((await res.json())['ok']);self.assertEqual(self.robot.sent,[])
 async def test_firmware_download_is_predictive_v4(self):
  res=await self.client.get('/api/firmware.ino');text=await res.text()
  self.assertIn('APPARENT_V4_FF',text);self.assertIn('esp32_level_predictive_v4.ino',res.headers['Content-Disposition'])
 async def test_level_button_selects_h_without_arming_or_saving_zero(self):
  self.robot.mode=0;self.lab.tray_modes['real']='OFF';self.lab.tray_data=self.robot.tray()
  d=self.body();d['tray_predictive']=False
  res=await self.client.post('/api/depth-route-start',json=d,headers=self.headers);data=await res.json()
  self.assertTrue(data['ok'],data);self.assertEqual(self.robot.tray_sent,['h'])
  goal=next(x for x in self.robot.sent if x['op']=='start');self.assertTrue(goal['tray_level']);self.assertFalse(goal['tray_predictive'])
 async def test_large_tilt_never_starts_or_changes_zero(self):
  self.robot.roll=30.;self.lab.tray_data=self.robot.tray()
  res=await self.client.post('/api/depth-route-start',json=self.body(),headers=self.headers)
  self.assertFalse((await res.json())['ok']);self.assertEqual(self.robot.sent,[]);self.assertEqual(self.robot.tray_sent,[])
 async def test_stop_during_level_preparation_cancels_late_start(self):
  import asyncio
  self.robot.mode=0;self.robot.roll=3.;self.lab.tray_modes['real']='OFF';self.lab.tray_data=self.robot.tray()
  async def start():
   res=await self.client.post('/api/depth-route-start',json=self.body(),headers=self.headers);return await res.json()
  pending=asyncio.create_task(start());await asyncio.sleep(.1)
  stop=await self.client.post('/api/stop',json={},headers=self.headers);self.assertTrue((await stop.json())['ok'])
  result=await pending;self.assertFalse(result['ok']);self.assertIn('취소',result['error']);self.assertEqual(self.robot.sent,[])

if __name__=='__main__':unittest.main()
