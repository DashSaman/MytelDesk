/* User-visible product regressions. Real React, mocked API, no target credentials/calls. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {start,contacts}=require('./test-web-product-fixture.cjs');
const out=path.resolve(process.env.MT_PRODUCT_OUTPUT||fs.mkdtempSync(path.join(os.tmpdir(),'myteldesk-product-')));
fs.mkdirSync(out,{recursive:true});
const result={scope:'loopback actual frontend; synthetic network; no production actions',cases:[]};
function persist(){fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2)+'\n');}
const json=(route,data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
let app;
async function check(name,run,options={}){
 const session=await app.session(options);
 try{await run(session);assert.deepEqual(session.errors,[]);result.cases.push({name,pass:true});console.log('PASS '+name);}
 catch(e){result.cases.push({name,pass:false,error:e.message});console.log('FAIL '+name+': '+e.message);}
 finally{await session.close();persist();}
}
(async()=>{
 app=await start();
 for(const noun of['users','groups']){
  let fail=true;
  await check(noun+' load failures are visible and recoverable without a reload',async s=>{
   await s.goto(noun);assert.equal(await s.page.locator('main [role=alert]').count(),1,'HTTP500 must not masquerade as an empty list');
   fail=false;await s.page.locator('main [role=alert] button').click();await s.page.waitForTimeout(450);
   assert.equal(await s.page.locator('main [role=alert]').count(),0);
   assert.ok(await s.page.locator('main tbody tr').count()>0);
  },{override:async(route,url)=>{
   if(fail&&url.pathname==='/MytellDesk/api/settings/'+noun){await json(route,{message:'Temporary fixture failure'},500);return true;}
  }});
 }
 let analyticsFail=true;
 await check('failed analytics settings cannot save fabricated defaults and can retry',async s=>{
  await s.goto('settings?tab=analytics');
  assert.ok(await s.page.locator('main [role=alert]').count()>0,'Settings load error must be visible');
  assert.equal(await s.page.locator('main .ui-card-footer button:not(:disabled)').count(),0,'Unknown settings cannot be saved');
  analyticsFail=false;await s.page.locator('main [role=alert] button').first().click();await s.page.waitForTimeout(450);
  assert.equal(await s.page.locator('#an-sla').inputValue(),'20');
 },{override:async(route,url)=>{if(analyticsFail&&url.pathname==='/MytellDesk/api/analytics/settings'){await json(route,{message:'Fixture unavailable'},500);return true;}}});
 await check('latest contact search wins when older response arrives last',async s=>{
  await s.goto('contacts');const input=s.page.locator('main input[type=search]');
  await input.fill('آفتاب');await s.page.waitForTimeout(350);await input.fill('سپهر');await s.page.waitForTimeout(1800);
  const rows=await s.page.locator('main tbody').innerText();assert.ok(rows.includes('گروه سپهر'));assert.ok(!rows.includes('شرکت آفتاب'));
 },{override:async(route,url)=>{
  if(url.pathname==='/MytellDesk/api/contacts'&&url.searchParams.has('search')){
   const old=url.searchParams.get('search')==='آفتاب';if(old)await new Promise(r=>setTimeout(r,1200));
   await json(route,{contacts:[contacts[old?0:1]],next_cursor:null});return true;
  }
 }});
 await check('contact filter changes never reuse the old cursor',async s=>{
  await s.goto('contacts');await s.page.locator('.ui-pagination button').last().click();await s.page.waitForTimeout(250);
  await s.page.locator('main input[type=search]').fill('سپهر');await s.page.waitForTimeout(600);
  const matching=s.requests.filter(r=>r.path.endsWith('/contacts')&&new URLSearchParams(r.search).has('search'));
  assert.ok(matching.length>0);assert.ok(matching.every(r=>!new URLSearchParams(r.search).has('cursor')));
 },{override:async(route,url)=>{if(url.pathname==='/MytellDesk/api/contacts'){await json(route,{contacts,next_cursor:'fixture-old-cursor'});return true;}}});
 await check('latest recording search wins when older response arrives last',async s=>{
  await s.goto('recordings');const input=s.page.locator('main input[type=search]');
  await input.fill('201');await s.page.waitForTimeout(350);await input.fill('202');await s.page.waitForTimeout(1800);
  assert.ok((await s.page.locator('main tbody').innerText()).includes('202'));
  assert.ok(!(await s.page.locator('main tbody').innerText()).includes('201'));
 },{override:async(route,url)=>{
  if(url.pathname==='/MytellDesk/api/recordings'&&url.searchParams.has('search')){
   const old=url.searchParams.get('search')==='201';if(old)await new Promise(r=>setTimeout(r,1200));
   await json(route,{recordings:[{calldate:'2026-11-15T09:15:00+03:30',src:old?'201':'202',dst:'600',duration:60,billsec:55,recordingfile:'fixture.wav',uniqueid:'fixture',recording_path:null}],total:1,next_cursor:null});return true;
  }
 }});
 await check('RTL tabs move focus in the visual arrow direction',async s=>{
  await s.goto('analytics');const tabs=s.page.locator('main [role=tab]');await tabs.first().focus();await s.page.keyboard.press('ArrowLeft');
  assert.equal(await tabs.nth(1).evaluate(e=>e===document.activeElement),true);
  assert.equal(await tabs.first().getAttribute('aria-selected'),'true','Arrows must not activate network fetches');
 });
 await check('active page tab is associated with its content panel',async s=>{
  await s.goto('analytics');const tab=s.page.locator('main [role=tab][aria-selected=true]');
  const id=await tab.getAttribute('aria-controls');assert.ok(id);assert.equal(await s.page.locator('[id="'+id+'"][role=tabpanel]').count(),1);
 });
 await check('analytics selected report survives a deep link and refresh',async s=>{
  await s.goto('analytics?tab=queues');assert.equal(await s.page.locator('#tab-queues').getAttribute('aria-selected'),'true');
  await s.page.reload({waitUntil:'networkidle'});assert.equal(await s.page.locator('#tab-queues').getAttribute('aria-selected'),'true');
 });
 await check('Persian search clear control has a Persian accessible name',async s=>{
  await s.goto('contacts');await s.page.locator('main input[type=search]').fill('نمونه');
  const name=await s.page.locator('.ui-search-clear').getAttribute('aria-label');assert.ok(/[\u0600-\u06ff]/.test(name));assert.ok(!name.startsWith('Clear '));
 });
 await check('recording dates follow Persian UI even on an English browser',async s=>{
  await s.goto('recordings');const text=await s.page.locator('main tbody tr').first().innerText();
  assert.ok(text.includes('۱۴۰۵'),'Persian calendar year must be used in Persian UI');assert.ok(!text.includes('AM'));
 });
 await check('contact error recovery keeps the typed filter',async s=>{
  await s.goto('contacts');await s.page.locator('main input[type=search]').fill('سپهر');await s.page.waitForTimeout(400);
  assert.equal(await s.page.getByRole('button',{name:'تلاش مجدد',exact:true}).count(),1,'Load failure has Retry');
  await s.page.getByRole('button',{name:'تلاش مجدد',exact:true}).click();await s.page.waitForTimeout(400);
  assert.equal(await s.page.locator('main input[type=search]').inputValue(),'سپهر');
 },{override:async(route,url)=>{if(url.pathname==='/MytellDesk/api/contacts'&&url.searchParams.has('search')){await json(route,{message:'Fixture unavailable'},500);return true;}}});
 await check('mobile brand and account controls fit within the header',async s=>{
  await s.goto('contacts');assert.equal(await s.page.locator('.header').evaluate(e=>{
   const box=e.getBoundingClientRect(),title=e.querySelector('.header-title').getBoundingClientRect();return title.top>=box.top&&title.bottom<=box.bottom;
  }),true);
 },{width:390});
 await check('opening inner settings tabs never scrolls the sidebar outside the viewport',async s=>{
  await s.goto('settings?tab=recording');const box=await s.page.locator('.sidebar').boundingBox();assert.ok(box.x>=0&&box.x+box.width<=1441);
  assert.ok((await s.page.locator('main').boundingBox()).width<1250);
 });
 await check('recording settings expose real VAD readiness and a retry for unavailable status',async s=>{
  await s.goto('settings?tab=recording');assert.equal(await s.page.locator('[data-vad-state=unavailable]').count(),1);
  assert.equal(await s.page.locator('[data-vad-state=unavailable]').getByRole('button',{name:'تلاش مجدد',exact:true}).count(),1);
 },{override:async(route,url)=>{if(url.pathname==='/MytellDesk/api/vad/status'){await json(route,{available:false,engine:null,error_code:'vad.unavailable'});return true;}}});
 console.log(JSON.stringify({passed:result.cases.filter(x=>x.pass).length,total:result.cases.length,out}));
 process.exitCode=result.cases.some(x=>!x.pass)?1:0;
})().catch(e=>{result.setupError=e.stack;persist();console.error(e);process.exitCode=2;}).finally(async()=>{persist();await app?.close();});
