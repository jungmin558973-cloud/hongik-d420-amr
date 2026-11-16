const {test}=require('node:test'),assert=require('node:assert/strict'),http=require('node:http'),fs=require('node:fs/promises'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
test('L-test UI forwards default brake/speed and optional brake-off to local API fixture',async t=>{
 const root=path.resolve(__dirname,'../web'),requests=[];let stops=0;
 const state={active:false,ready:true,mapping_mode:true,manual_ready:true,busy:false,maps:[],nav:{ready:false},map:null,path:[],reason:'fixture',message:'대기',run_message:'대기'};
 const run={kind:'brake',depth_route:{ready:true},camera:{available:false},tray:{connected:true,imu_fresh:true,roll_deg:0,pitch_deg:0},active:false};let mainState=null;
 const server=http.createServer(async(req,res)=>{
  const u=new URL(req.url,'http://localhost');
  if(u.pathname.startsWith('/api/')){
   let raw='';for await(const c of req)raw+=c;
   let value;
   if(u.pathname==='/api/session')value={token:'fixture',host:'fixture'};
   else if(u.pathname==='/api/navigation')value=state;
   else if(u.pathname==='/api/status')value=run;
   else if(u.pathname==='/api/experiments')value={ok:true,experiments:[{run_id:'browser-experiment-123',started_unix:1800000000,kind:'depth-route',mode:'real',status:'stopped',reason:'회전 관측 대기',sample_count:23}]};
   else if(u.pathname==='/api/experiment-cases')value={speeds_cm_s:[20,30,40],cases:['baseline','level','active','scurve','combined'].map(id=>({id,label:id}))};
   else if(u.pathname==='/api/depth-route-start'){requests.push(JSON.parse(raw));state.active=run.active=true;run.kind='depth-route';run.execution_host='Jetson';value={ok:true,state:{...run,active:true,message:'시험 요청 확인'}};}
   else if(u.pathname==='/api/stop'){stops++;state.active=run.active=false;value={ok:true,state:{...(mainState||run),active:false,state:'stopped',message:'사용자가 중지했습니다.'}};}
   else if(u.pathname==='/api/teleop-start'){state.active=run.active=true;run.kind='teleop';value={ok:true,state:{active:true,control_id:'manual-fixture'}};}
   else if(u.pathname==='/api/teleop-input'||u.pathname==='/api/heartbeat')value={ok:true,state:run};
   else value={ok:false,error:'fixture does not run ROS'};
   res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(value));return;
  }
  try{
   const name=u.pathname==='/'?'navigation.html':u.pathname==='/experiment-setup'?'experiment_setup.html':u.pathname==='/experiments'?'experiments.html':u.pathname.replace(/^\/static\//,'').replace(/^\//,''),p=path.resolve(root,name);
   if(!p.startsWith(root+path.sep))throw Error();
   const data=await fs.readFile(p);res.writeHead(200,{'Content-Type':name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html'});res.end(data);
  }catch{res.writeHead(404);res.end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;
 t.after(async()=>{if(browser)await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));});
 browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(8000);
 await page.goto('http://127.0.0.1:'+server.address().port);
 await page.waitForFunction(()=>document.getElementById('depth-status').textContent.includes('전방 깊이 수신 중')&&!document.getElementById('depth-go').disabled);
 assert.equal(await page.locator('#depth-speed').inputValue(),'12');assert.equal(await page.locator('#depth-brake').isChecked(),true);
 assert.equal(await page.locator('#depth-extra').inputValue(),'30');
 assert.match(await page.locator('#depth-go').textContent(),/전체 실험 시작/);assert.match(await page.locator('#depth-trial').textContent(),/회전 안 함/);
 assert.equal(await page.locator('a[href="/experiments"]').count(),1);
 await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/depth-route-start')),page.locator('#depth-go').click()]);
 await page.waitForFunction(()=>document.getElementById('depth-go').disabled);
 await page.evaluate(()=>{window.dispatchEvent(new Event('blur'));window.dispatchEvent(new Event('pagehide'));});
 await page.route('**/api/navigation',r=>r.abort());await page.route('**/api/status',r=>r.abort());await page.waitForTimeout(600);assert.equal(stops,0,'local route continues through focus/network loss');
 await page.unroute('**/api/navigation');await page.unroute('**/api/status');state.active=run.active=false;
 await page.waitForFunction(()=>!document.getElementById('depth-go').disabled);
 assert.equal(requests[0].brake,true);assert.equal(requests[0].config.speed,.12);assert.equal(requests[0].trial,false);
 assert.equal(requests[0].turn_extension,.3);
 await page.locator('#depth-tray').check();await page.locator('#depth-lead').fill('250');await page.locator('#depth-angle').fill('1.5');
 await page.locator('#depth-brake').uncheck();await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/depth-route-start')),page.locator('#depth-trial').click()]);
 assert.equal(requests[1].brake,false);assert.equal(requests[1].trial,true);
 assert.equal(requests[0].tray_level,true);
 assert.equal(requests[0].tray_predictive,false);assert.equal(requests[1].tray_predictive,true);assert.equal(requests[1].tray_prediction.lead,.25);assert.equal(requests[1].tray_prediction.angle_limit,1.5);
 await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/stop')),page.locator('#stop').click()]);assert.equal(stops,1,'explicit STOP still stops local route');
 await page.waitForFunction(()=>!document.getElementById('manual').disabled);
 await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/teleop-start')),page.locator('#manual').click()]);
 await page.evaluate(()=>window.dispatchEvent(new Event('blur')));await page.waitForFunction(()=>document.getElementById('manual').textContent.includes('켜기'));assert.equal(stops,2,'manual focus loss still stops');
 await page.waitForFunction(()=>document.querySelectorAll('#experiment-matrix button').length===15);
 await page.locator('#experiment-repeat').fill('3');
 await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/depth-route-start')),page.locator('#experiment-matrix button[data-case="combined"][data-speed="40"]').click()]);
 assert.equal(requests[2].experiment_case,'combined');assert.equal(requests[2].config.speed,.4);assert.equal(requests[2].repeat,3);assert.equal(requests[2].brake,true);
 await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/stop')),page.locator('#stop').click()]);
 Object.assign(run,{active:false,state:'stopped',message:'진행 없음 · 첫 직선',stop_context:{phase:'depth_first'}});
 await page.waitForFunction(()=>document.getElementById('stop-alert')&&!document.getElementById('stop-alert').hidden);
 assert.match(await page.locator('#stop-alert').textContent(),/진행 없음 · 첫 직선/);
 assert.match(await page.locator('#stop-alert').textContent(),/발생 단계: 첫 직선/);
 await page.locator('#stop-alert button').click();assert.equal(await page.locator('#stop-alert').isVisible(),false);
 await page.screenshot({path:path.resolve(__dirname,'../L_TEST_UI.png'),fullPage:true});assert.deepEqual(errors,[]);
 await page.goto('http://127.0.0.1:'+server.address().port+'/experiments');
 await page.waitForFunction(()=>document.getElementById('rows').textContent.includes('회전 관측 대기'));
 assert.equal(await page.locator('a[href="/api/experiments/browser-experiment-123.csv"]').textContent(),'CSV');
 assert.equal(await page.locator('a[href="/api/experiments/browser-experiment-123.json"]').textContent(),'JSON');
 await page.screenshot({path:path.resolve(__dirname,'../EXPERIMENTS_UI.png'),fullPage:true});assert.deepEqual(errors,[]);
 mainState=JSON.parse(await fs.readFile(path.join(__dirname,'local_mission_fixture.json'),'utf8'));
 await page.routeWebSocket('**/ws',ws=>ws.send(JSON.stringify(mainState)));
 await page.goto('http://127.0.0.1:'+server.address().port+'/index.html');
 await page.waitForFunction(()=>document.getElementById('run-stage')?.textContent.includes('첫 직선'));
 const beforeClose=stops;
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));window.dispatchEvent(new Event('pagehide'));});
 await page.waitForTimeout(150);assert.equal(stops,beforeClose,'main tray/scenario tab must not cancel a Jetson L mission on hide/close');
 await page.locator('#quick-stop').click();await page.waitForTimeout(150);assert.equal(stops,beforeClose+1,'explicit stop from main tab remains available');
 assert.deepEqual(errors,[]);
 await page.goto('http://127.0.0.1:'+server.address().port+'/experiment-setup');state.active=run.active=false;
 await page.waitForFunction(()=>!document.getElementById('brake').disabled);
 await page.locator('#speed').selectOption('30');await page.locator('#strength').fill('50');
 await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/depth-route-start')),page.locator('#brake').click()]);
 assert.equal(requests[3].experiment_options.test,'brake_line');assert.equal(requests[3].experiment_options.smooth_pct,50);assert.equal(requests[3].config.speed,.3);assert.equal(requests[3].experiment_options.turn_rate,.6);
 const setupStops=stops;await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));await page.waitForTimeout(100);assert.equal(stops,setupStops);
 await page.screenshot({path:path.resolve(__dirname,'../EXPERIMENT_SETUP_UI.png'),fullPage:true});assert.deepEqual(errors,[]);
});
