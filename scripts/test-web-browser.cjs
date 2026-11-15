/* Native React app served by local Vite. All API/socket traffic uses synthetic
   fixtures; no target PBX, credentials, microphone or telephone is contacted. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {createRequire}=require('node:module');
const {pathToFileURL}=require('node:url');
const {chromium}=require('playwright');
const frontend=path.resolve(__dirname,'../frontend');
const requireFrontend=createRequire(path.join(frontend,'package.json'));
const out=process.env.MT_WEB_OUTPUT?path.resolve(process.env.MT_WEB_OUTPUT):fs.mkdtempSync(path.join(os.tmpdir(),'myteldesk-web-'));
fs.mkdirSync(out,{recursive:true});
const result={scope:'local actual React components with synthetic API/WS state',cases:[],errors:[],blockedExternal:[]};
const user={id:1,username:'fixture-admin',name:'Fixture Admin',role:'admin',extension:null,monitor_modes:['listen']};
const state={extensions:{},active_calls:{},queues:{},queue_members:{},queue_entries:{},agent_states:{},stats:{total_extensions:0,online_extensions:0,total_queues:0,total_waiting:0,active_calls_count:0}};
let server,browser,origin;
function persist(){fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2)+'\n');}
async function check(name,body){try{await body();result.cases.push({name,pass:true});console.log('PASS '+name);}catch(e){result.cases.push({name,pass:false,error:e.message});console.log('FAIL '+name+': '+e.message);}persist();}
function responseFor(pathname){
 const apiPath=pathname.replace(/^\/MytellDesk/,'');
 if(apiPath==='/api/auth/me')return user;
 if(apiPath==='/api/webrtc/config')return{server:'',extension:null,extension_secret:null};
 if(apiPath==='/api/settings/extensions/webrtc')return{extensions:[]};
 if(apiPath==='/api/call-notifications')return{notifications:[],total:0};
 if(apiPath==='/api/call-log')return{calls:[],total:0};
 if(apiPath==='/api/analytics/overview')return{};
 return{};
}
async function withPage(run,{width=390,theme='light'}={}){
 const context=await browser.newContext({viewport:{width,height:900},serviceWorkers:'block'});
 const requests=[],errors=[];
 await context.addInitScript(({user,theme})=>{
  localStorage.setItem('opdesk_token','fixture-token');localStorage.setItem('opdesk_user',JSON.stringify(user));
  localStorage.setItem('opdesk:theme',theme);localStorage.setItem('opdesk-lang','en');
  window.__workerRegistrations=[];
  if('serviceWorker' in navigator)Object.defineProperty(navigator.serviceWorker,'register',{value:async(url,options)=>{
   window.__workerRegistrations.push({url:String(url),scope:options?.scope||null});return{scope:new URL('./',new URL(url,location.href)).href};
  }});
 },{user,theme});
 await context.routeWebSocket('**/*',socket=>{
  const u=new URL(socket.url());if(u.origin.replace(/^ws/,'http')!==origin||u.pathname!=='/MytellDesk/ws'){return socket.close();}
  socket.onMessage(message=>{let data;try{data=JSON.parse(String(message));}catch{}
   if(data&&Object.keys(data).length===1&&data.token==='fixture-token')socket.send(JSON.stringify({type:'initial_state',data:state}));
   else throw new Error('Unexpected call/control action in fixture');
  });
 });
 await context.route('**/*',route=>{
  const request=route.request(),u=new URL(request.url());requests.push({path:u.pathname,type:request.resourceType(),method:request.method()});
  if(u.origin!==origin){result.blockedExternal.push(u.origin);return route.abort('blockedbyclient');}
  if(/^\/(?:MytellDesk\/)?api\//.test(u.pathname))return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(responseFor(u.pathname))});
  if(!['GET','HEAD','OPTIONS'].includes(request.method()))throw new Error('Unexpected non-API fixture mutation');
  return route.continue();
 });
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.goto(origin+'/MytellDesk/extensions',{waitUntil:'networkidle',timeout:45000});
  await page.locator('.sidebar-nav').waitFor();await page.waitForTimeout(200);
  await run(page,requests);assert.deepEqual(errors,[],'React runtime errors');
 }finally{result.errors.push(...errors);await context.close();}
}
async function hiddenTabStops(page,kind){
 await page.evaluate(()=>document.activeElement?.blur());const found=[];
 for(let i=0;i<45;i++){
  await page.keyboard.press('Tab');
  const hit=await page.evaluate(kind=>{const e=document.activeElement;return kind==='phone'?!!e.closest('.softphone-popover[aria-hidden="true"]'):!!e.closest('.sidebar:not(.open)');},kind);
  if(hit)found.push(i+1);
 }
 return found;
}
(async()=>{
 const vite=await import(pathToFileURL(requireFrontend.resolve('vite')).href);
 const react=(await import(pathToFileURL(requireFrontend.resolve('@vitejs/plugin-react')).href)).default;
 await check('default build base is the standalone desk path',async()=>{
  const config=await vite.resolveConfig({root:frontend,configFile:path.join(frontend,'vite.config.ts')},'build');assert.equal(config.base,'/MytellDesk/');
 });
 server=await vite.createServer({root:frontend,configFile:false,base:'/MytellDesk/',plugins:[react()],logLevel:'error',server:{host:'127.0.0.1',port:0,hmr:false}});
 await server.listen();origin='http://127.0.0.1:'+server.httpServer.address().port;
 browser=await chromium.launch({headless:true,...(process.env.MT_TEST_BROWSER==='chromium'?{}:{channel:process.env.MT_TEST_BROWSER||'chrome'})});
 await check('closed dialer controls are skipped by Tab',()=>withPage(async page=>{assert.deepEqual(await hiddenTabStops(page,'phone'),[]);}));
 await check('closed mobile drawer controls are skipped by Tab',()=>withPage(async page=>{assert.deepEqual(await hiddenTabStops(page,'sidebar'),[]);}));
 await check('opening mobile navigation moves keyboard focus into it',()=>withPage(async page=>{
  await page.locator('.header-hamburger').click();await page.waitForTimeout(100);
  assert.equal(await page.locator('.sidebar').evaluate(e=>e.contains(document.activeElement)),true);
 }));
 await check('Escape closes mobile navigation and restores its trigger',()=>withPage(async page=>{
  await page.locator('.header-hamburger').click();await page.keyboard.press('Escape');
  assert.equal(await page.locator('.sidebar').evaluate(e=>e.classList.contains('open')),false);
  assert.equal(await page.locator('.header-hamburger').evaluate(e=>e===document.activeElement),true);
 }));
 await check('drawer keeps Tab inside while open',()=>withPage(async page=>{
  await page.locator('.header-hamburger').click();await page.waitForTimeout(100);
  for(let i=0;i<18;i++){await page.keyboard.press('Tab');assert.equal(await page.locator('.sidebar').evaluate(e=>e.contains(document.activeElement)),true);}
 }));
 await check('desktop navigation remains focusable after mobile resize',()=>withPage(async page=>{
  await page.locator('.header-hamburger').click();await page.setViewportSize({width:1440,height:900});await page.waitForTimeout(100);
  await page.locator('.sidebar-item').first().focus();assert.equal(await page.locator('.sidebar-item').first().evaluate(e=>e===document.activeElement),true);
  await page.setViewportSize({width:390,height:900});await page.waitForTimeout(100);
  assert.equal(await page.locator('.sidebar').evaluate(e=>e.classList.contains('open')),false);
 }));
 await check('dialer opens with focus and a named number input',()=>withPage(async page=>{
  await page.locator('.softphone-fab').click();await page.waitForTimeout(200);
  assert.equal(await page.locator('.softphone-popover').evaluate(e=>e.contains(document.activeElement)),true);
  assert.equal(await page.locator('.softphone-search-input').evaluate(e=>!!(e.labels?.length||e.getAttribute('aria-label')||e.getAttribute('aria-labelledby'))),true);
 }));
 await check('dialer Escape returns focus without replacing the audio node',()=>withPage(async page=>{
  await page.evaluate(()=>window.__audioBefore=document.querySelector('.softphone-popover audio'));
  await page.locator('.softphone-fab').click();await page.waitForTimeout(200);await page.keyboard.press('Escape');await page.waitForTimeout(200);
  assert.equal(await page.locator('.softphone-popover').getAttribute('aria-hidden'),'true');
  assert.equal(await page.locator('.softphone-fab').evaluate(e=>e===document.activeElement),true);
  assert.equal(await page.evaluate(()=>!!window.__audioBefore&&window.__audioBefore===document.querySelector('.softphone-popover audio')),true);
 }));
 await check('all native requests stay under the desk base without root aliases',()=>withPage(async(page,requests)=>{
  assert.deepEqual(requests.filter(r=>r.path.startsWith('/api/')),[]);
  assert.deepEqual(requests.filter(r=>r.type==='font'&&!r.path.startsWith('/MytellDesk/')),[]);
 }));
 await check('service worker is registered only under the desk base',()=>withPage(async page=>{
  const entries=await page.evaluate(()=>window.__workerRegistrations);assert.equal(entries.length,1);
  assert.equal(new URL(entries[0].url,origin).pathname,'/MytellDesk/sw.js');
 }));
 await check('both themes preserve mobile layout after focus repairs',async()=>{
  for(const theme of ['light','dark'])await withPage(async page=>{
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   await page.locator('.header-hamburger').click();await page.waitForTimeout(200);
   await page.locator('.sidebar').screenshot({path:path.join(out,'drawer-'+theme+'.png')});
  },{theme});
 });
 if(result.blockedExternal.length)throw new Error('Fixture attempted an external request');
 console.log(JSON.stringify({pass:result.cases.filter(x=>x.pass).length,total:result.cases.length,output:out}));
 process.exitCode=result.cases.some(x=>!x.pass)?1:0;
})().catch(e=>{result.setupError=e.message;process.exitCode=2;console.error(e);}).finally(async()=>{persist();await browser?.close();await server?.close();});
