const {test}=require('node:test'),assert=require('node:assert/strict'),http=require('node:http'),fs=require('node:fs/promises'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
test('L-test UI forwards default brake/speed and optional brake-off to local API fixture',async t=>{
 const root=path.resolve(__dirname,'../web'),requests=[];
 const state={active:false,ready:true,mapping_mode:true,manual_ready:true,busy:false,maps:[],nav:{ready:false},map:null,path:[],reason:'fixture',message:'대기',run_message:'대기'};
 const run={kind:'brake',depth_route:{ready:true},camera:{available:false},active:false};
 const server=http.createServer(async(req,res)=>{
  const u=new URL(req.url,'http://localhost');
  if(u.pathname.startsWith('/api/')){
   let raw='';for await(const c of req)raw+=c;
   let value;
   if(u.pathname==='/api/session')value={token:'fixture',host:'fixture'};
   else if(u.pathname==='/api/navigation')value=state;
   else if(u.pathname==='/api/status')value=run;
   else if(u.pathname==='/api/depth-route-start'){requests.push(JSON.parse(raw));value={ok:true,state:{...run,active:true,message:'시험 요청 확인'}};}
   else value={ok:false,error:'fixture does not run ROS'};
   res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(value));return;
  }
  try{
   const name=u.pathname==='/'?'navigation.html':u.pathname.replace(/^\/static\//,'').replace(/^\//,''),p=path.resolve(root,name);
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
 await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/depth-route-start')),page.locator('#depth-go').click()]);await page.waitForFunction(()=>!document.getElementById('depth-go').disabled);
 assert.equal(requests[0].brake,true);assert.equal(requests[0].config.speed,.12);assert.equal(requests[0].trial,false);
 await page.locator('#depth-brake').uncheck();await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/depth-route-start')),page.locator('#depth-trial').click()]);
 assert.equal(requests[1].brake,false);assert.equal(requests[1].trial,true);
 await page.screenshot({path:path.resolve(__dirname,'../L_TEST_UI.png'),fullPage:true});assert.deepEqual(errors,[]);
});
