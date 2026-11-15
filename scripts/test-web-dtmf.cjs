/* Real Softphone, useWebPhone and WebPhone.sendDTMF; only SIP transport/session
   and media are synthetic. No microphone, registration or PBX is contacted. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {createRequire}=require('node:module'),{pathToFileURL}=require('node:url');
const {chromium}=require('playwright');
const frontend=path.resolve(__dirname,'../frontend'),requireFrontend=createRequire(path.join(frontend,'package.json'));
const out=process.env.MT_WEB_OUTPUT||fs.mkdtempSync(path.join(os.tmpdir(),'myteldesk-dtmf-'));
fs.mkdirSync(out,{recursive:true});
const result={scope:'native Softphone/hook/sendDTMF with synthetic Established SIP session; no wire proof',cases:[]};
const entry=`
import React from 'react'; import {createRoot} from 'react-dom/client';
import {SessionState,RegistererState} from 'sip.js';
import {Softphone} from '/src/components/Softphone';
import {useWebPhone} from '/src/hooks/useWebPhone';
import {WebPhoneProvider} from '/src/contexts/WebPhoneContext';
import {WebPhone} from '/src/lib/webPhone';
import '/src/i18n'; import '/src/styles/variables.css'; import '/src/styles/index.css';
import '/src/styles/utilities.css'; import '/src/styles/mytell-workspace.css';
window.fixture={sent:[],calls:[],state:'idle'};
const fixture=window.fixture;
WebPhone.prototype.connect=async function(){fixture.phone=this;this.registerer={state:RegistererState.Registered};this.callbacks.onStatus('connected');};
WebPhone.prototype.disconnect=function(){};
WebPhone.prototype.makeCall=async function(number){fixture.calls.push(['makeCall',number]);};
WebPhone.prototype.getStats=async()=>null;
WebPhone.prototype.toggleMute=function(){fixture.calls.push(['mute']);this.callbacks.onMutedChange(!fixture.hook.isMuted);};
WebPhone.prototype.hold=async()=>{fixture.calls.push(['hold']);};
WebPhone.prototype.unhold=async()=>{fixture.calls.push(['unhold']);};
WebPhone.prototype.hangup=function(){fixture.calls.push(['hangup']);fixture.setState('idle');};
fixture.setState=state=>{
 fixture.state=state;
 fixture.phone.session=state==='idle'?null:{state:state==='answered'?SessionState.Established:SessionState.Establishing,sessionDescriptionHandler:{sendDtmf:digit=>fixture.sent.push(digit)}};
 fixture.phone.callbacks.onCallStatus(state==='answered'?'In call with 9915190':state==='ringing'?'Ringing...':'');
};
function Harness(){const hook=useWebPhone();fixture.hook=hook;return <WebPhoneProvider value={hook}><main style={{maxWidth:360,margin:'24px auto'}}><Softphone/></main></WebPhoneProvider>;}
document.documentElement.dataset.theme=localStorage.getItem('opdesk:theme')||'light';
createRoot(document.getElementById('root')).render(<Harness/>);
`;
let server,browser,origin;
function persist(){fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2)+'\n');}
async function check(name,fn,options={}){
 const context=await browser.newContext({viewport:{width:390,height:1000},serviceWorkers:'block'}),errors=[];
 await context.addInitScript(({theme})=>{
  localStorage.setItem('opdesk-lang','en');localStorage.setItem('opdesk:theme',theme);
  localStorage.setItem('opdesk_token','fixture');HTMLMediaElement.prototype.play=async()=>{};
  if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=async()=>{throw new Error('Real media forbidden');};
 },{theme:options.theme||'light'});
 await context.routeWebSocket('**/*',socket=>socket.close());
 await context.route('**/*',route=>{
  const u=new URL(route.request().url());
  if(u.origin!==origin){errors.push('External request');return route.abort();}
  if(u.pathname.includes('/api/'))return route.fulfill({contentType:'application/json',body:JSON.stringify(u.pathname.endsWith('/webrtc/config')?{server:'wss://fixture.invalid',extension:'201',extension_secret:'fixture'}:{reasons:[]})});
  return route.continue();
 });
 const page=await context.newPage();page.setDefaultTimeout(4000);page.setDefaultNavigationTimeout(45000);page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.goto(origin+'/MytellDesk/__dtmf',{waitUntil:'networkidle'});
  await page.waitForFunction(()=>window.fixture?.phone&&window.fixture.hook.isConnected).catch(async e=>{throw new Error(e.message+'; '+JSON.stringify({errors,body:await page.locator('body').innerText(),fixture:await page.evaluate(()=>({hasFixture:!!window.fixture,hookStatus:window.fixture?.hook?.status,config:window.fixture?.hook?.config}))}));});
  await fn(page);assert.deepEqual(errors,[]);result.cases.push({name,pass:true});console.log('PASS '+name);
 }catch(e){result.cases.push({name,pass:false,error:e.message});console.log('FAIL '+name+': '+e.message);}
 finally{await context.close();persist();}
}
async function answered(page){await page.evaluate(()=>fixture.setState('answered'));await page.locator('.softphone-incall').waitFor();}
async function openPad(page){await page.getByRole('button',{name:'Keypad',exact:true}).click();await page.getByRole('group',{name:'In-call keypad'}).waitFor();}
(async()=>{
 const vite=await import(pathToFileURL(requireFrontend.resolve('vite')).href);
 const react=(await import(pathToFileURL(requireFrontend.resolve('@vitejs/plugin-react')).href)).default;
 const id='virtual:dtmf-fixture.jsx';
 const plugin={name:'dtmf-fixture',resolveId(x){if(x===id||x.endsWith('/'+id))return '\0'+id;},async load(x){if(x==='\0'+id)return (await vite.transformWithEsbuild(entry,'dtmf-fixture.tsx',{loader:'tsx',jsx:'automatic'})).code;},configureServer(s){s.middlewares.use(async(req,res,next)=>{if(!req.url?.endsWith('/__dtmf'))return next();res.setHeader('Content-Type','text/html');res.end(await s.transformIndexHtml(req.url,'<!doctype html><html><head><title>DTMF fixture</title></head><body><div id="root"></div><script type="module" src="/MytellDesk/@id/'+id+'"></script></body></html>'));});}};
 server=await vite.createServer({root:frontend,configFile:false,base:'/MytellDesk/',plugins:[plugin,react()],logLevel:'error',server:{host:'127.0.0.1',port:0,hmr:false}});
 await server.listen();origin='http://127.0.0.1:'+server.httpServer.address().port;
 browser=await chromium.launch({headless:true,channel:process.env.MT_TEST_BROWSER||'chrome',args:['--mute-audio']});
 await check('transport rejects tones before answer and invalid digits',async page=>{
  await page.evaluate(()=>{fixture.setState('ringing');fixture.phone.sendDTMF('4');fixture.setState('answered');for(const x of ['','12','A','x'])fixture.phone.sendDTMF(x);});
  assert.deepEqual(await page.evaluate(()=>fixture.sent),[]);
 });
 await check('keypad is absent while idle or ringing',async page=>{
  assert.equal(await page.getByRole('button',{name:'Keypad',exact:true}).count(),0);
  await page.evaluate(()=>fixture.setState('ringing'));await page.locator('.softphone-incall').waitFor();
  assert.equal(await page.getByRole('button',{name:'Keypad',exact:true}).count(),0);
 });
 await check('all twelve in-call buttons reach actual sendDTMF without editing the next call number',async page=>{
  await answered(page);await page.evaluate(()=>fixture.hook.setDialNumber('201'));await openPad(page);
  const pad=page.getByRole('group',{name:'In-call keypad'});
  for(const digit of '123456789*0#')await pad.getByRole('button',{name:digit,exact:true}).click();
  assert.deepEqual(await page.evaluate(()=>fixture.sent),[...'123456789*0#']);
  assert.equal(await page.evaluate(()=>fixture.hook.dialNumber),'201');
  assert.deepEqual(await page.evaluate(()=>fixture.calls),[]);
 });
 await check('scoped physical keys send digits once, ignore modifiers/repeat and Enter never places a call',async page=>{
  await answered(page);await openPad(page);const input=page.getByRole('textbox',{name:'Digits sent'});assert.equal(await input.evaluate(e=>e===document.activeElement),true);
  await input.pressSequentially('4012*#');await input.press('Enter');await input.press('Control+5');
  await input.dispatchEvent('keydown',{key:'8',repeat:true});
  assert.deepEqual(await page.evaluate(()=>fixture.sent),[...'4012*#']);
  assert.deepEqual(await page.evaluate(()=>fixture.calls),[]);
 });
 await check('Escape closes only the keypad and restores the toggle focus',async page=>{
  await answered(page);await openPad(page);await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('group',{name:'In-call keypad'}).count(),0);
  assert.equal(await page.getByRole('button',{name:'Keypad',exact:true}).evaluate(e=>e===document.activeElement),true);
  assert.deepEqual(await page.evaluate(()=>fixture.calls),[]);
 });
 await check('hold and mute remain usable with keypad open',async page=>{
  await answered(page);await openPad(page);await page.getByRole('button',{name:'Mute',exact:true}).click();await page.getByRole('button',{name:'Hold',exact:true}).click();
  await page.getByRole('group',{name:'In-call keypad'}).getByRole('button',{name:'#',exact:true}).click();
  assert.deepEqual(await page.evaluate(()=>fixture.calls.map(x=>x[0])),['mute','hold']);assert.deepEqual(await page.evaluate(()=>fixture.sent),['#']);
  assert.equal(await page.evaluate(()=>fixture.hook.isMuted&&fixture.hook.isOnHold),true);
 });
 await check('a finished call clears the keypad and cannot send a late digit',async page=>{
  await answered(page);await openPad(page);await page.getByRole('textbox',{name:'Digits sent'}).press('4');
  await page.evaluate(()=>fixture.setState('idle'));await page.locator('.softphone-search-input').waitFor();
  await page.evaluate(()=>fixture.phone.sendDTMF('5'));assert.deepEqual(await page.evaluate(()=>fixture.sent),['4']);
  await answered(page);await openPad(page);assert.equal(await page.getByRole('textbox',{name:'Digits sent'}).inputValue(),'');
 });
 for(const theme of ['light','dark'])await check('mobile '+theme+' keypad keeps all digits and hangup visible',async page=>{
  await answered(page);await openPad(page);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.equal(await page.getByRole('group',{name:'In-call keypad'}).getByRole('button').count(),12);
  await page.getByRole('button',{name:'End call',exact:true}).scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'dtmf-'+theme+'.png')});
 },{theme});
 process.exitCode=result.cases.some(x=>!x.pass)?1:0;
 console.log(JSON.stringify({pass:result.cases.filter(x=>x.pass).length,total:result.cases.length,out}));
})().catch(e=>{result.setupError=e.message;process.exitCode=2;console.error(e);}).finally(async()=>{persist();await browser?.close();await server?.close();});
