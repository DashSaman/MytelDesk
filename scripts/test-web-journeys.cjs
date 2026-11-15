/* Feature acceptance against the actual frontend and isolated synthetic services. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {start,contacts}=require('./test-web-product-fixture.cjs');
const out=path.resolve(process.env.MT_PRODUCT_OUTPUT||fs.mkdtempSync(path.join(os.tmpdir(),'myteldesk-journeys-')));fs.mkdirSync(out,{recursive:true});
const results={scope:'Mocked local feature journeys; no live media, data, calls or configuration writes',cases:[]};
const persist=()=>fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(results,null,2));
const json=(r,d,status=200)=>r.fulfill({status,contentType:'application/json',body:JSON.stringify(d)});
let app;
async function check(name,body,options={}){
 if(process.env.MT_PRODUCT_CASE&&!name.includes(process.env.MT_PRODUCT_CASE))return;
 const s=await app.session({lang:'en',...options});
 try{await body(s);assert.deepEqual(s.errors,[]);results.cases.push({name,pass:true});console.log('PASS '+name);}
 catch(e){results.cases.push({name,pass:false,error:e.message});console.log('FAIL '+name+': '+e.message);}
 finally{await s.close();persist();}
}
(async()=>{app=await start();
 await check('recording retention save keeps its confirmation after refreshing settings',async s=>{
  await s.goto('settings?tab=recording');await s.page.locator('#tab-local').click();await s.page.locator('#rec-days').fill('45');
  await s.page.locator('.ui-card-footer .btn-primary').click();await s.page.waitForTimeout(500);
  assert.ok((await s.page.locator('main [role=status]').allTextContents()).some(text=>text.includes('Retention settings saved')));
  assert.equal(s.requests.filter(r=>r.path==='/MytellDesk/api/recording/retention'&&r.method==='POST').length,1);
 });
 await check('partial team load keeps known users visible and disables unsafe editing',async s=>{
  await s.goto('users');assert.ok((await s.page.locator('main').innerText()).includes('demo-admin'));
  assert.equal(await s.page.locator('main .panel-header-actions .btn-primary').isDisabled(),true);
  assert.ok(await s.page.getByRole('button',{name:'Retry',exact:true}).count());
 },{override:async(route,url)=>{if(url.pathname==='/MytellDesk/api/settings/agents'){await json(route,{message:'Fixture unavailable'},500);return true;}}});
 await check('main navigation opens every available section',async s=>{
  await s.goto('dashboard');const items=await s.page.locator('.sidebar-item').allTextContents();
  for(const text of items){await s.page.locator('.sidebar-item').filter({hasText:text.trim()}).first().click();await s.page.waitForTimeout(150);assert.ok((await s.page.locator('main').innerText()).trim().length>0);}
 });
 await check('extension search, no-match recovery and density toggle',async s=>{
  await s.goto('extensions');await s.page.locator('main input[type=search]').fill('201');await s.page.waitForTimeout(300);assert.equal(await s.page.locator('.extension-card').count(),1);
  await s.page.locator('main input[type=search]').fill('not-found');await s.page.waitForTimeout(300);assert.equal(await s.page.locator('.ui-empty').count(),1);
  await s.page.getByRole('button',{name:'Clear filters',exact:true}).click();await s.page.waitForTimeout(250);assert.equal(await s.page.locator('.extension-card').count(),6);
  await s.page.locator('.panel-header button[aria-pressed]').click();assert.equal(await s.page.locator('.extensions-grid.compact').count(),1);
 });
 await check('active-call transfer validates destination and emits one mocked action',async s=>{
  await s.goto('calls');await s.page.locator('button[aria-expanded=false]').filter({has:s.page.locator('svg.lucide-arrow-right-left')}).click();
  assert.equal(await s.page.locator('.call-transfer .btn-primary').isDisabled(),true);await s.page.locator('.call-transfer input').fill('206');await s.page.locator('.call-transfer .btn-primary').click();
  await s.page.waitForTimeout(100);assert.equal(s.requests.filter(r=>r.method==='WS'&&r.action==='transfer').length,1);
 });
 await check('queue members modal can be inspected and cancelled without a control action',async s=>{
  await s.goto('queues');await s.page.getByRole('button',{name:'Add member',exact:true}).first().click();await s.page.getByRole('dialog').waitFor();
  await s.page.keyboard.press('Escape');await s.page.waitForTimeout(250);assert.equal(await s.page.getByRole('dialog').count(),0);assert.equal(s.requests.filter(r=>r.method==='WS').length,0);
 });
 await check('call history date preset updates the server query',async s=>{
  await s.goto('call-log');await s.page.locator('.ui-range-trigger').click();await s.page.getByRole('button',{name:'Today',exact:true}).click();await s.page.waitForTimeout(300);
  const r=s.requests.filter(r=>r.path.endsWith('/call-log')).at(-1),q=new URLSearchParams(r.search);assert.equal(q.get('date_from'),q.get('date_to'));assert.ok(q.get('date_from'));
 });
 await check('contact validation stays in the form, then a mocked save confirms success',async s=>{
  await s.goto('contacts');await s.page.locator('.panel-header-actions .btn-primary').click();
  await s.page.locator('.ui-modal-footer button[type=submit]').click();assert.equal(await s.page.locator('.ui-modal [role=alert]').count(),1);
  assert.equal(s.requests.filter(r=>r.method==='POST'&&r.path.endsWith('/contacts')).length,0);
  await s.page.locator('#contact-name').fill('Fixture contact');await s.page.locator('#contact-phone').fill('02100001999');
  await s.page.locator('.ui-modal-footer button[type=submit]').click();await s.page.waitForTimeout(350);
  assert.equal(await s.page.getByRole('dialog').count(),0);assert.equal(await s.page.locator('main .ui-alert--success').count(),1);
 });
 await check('contact save rejection retains editable values and permits retry',async s=>{
  await s.goto('contacts');await s.page.locator('.panel-header-actions .btn-primary').click();await s.page.locator('#contact-name').fill('Fixture contact');await s.page.locator('#contact-phone').fill('02100001999');
  await s.page.locator('.ui-modal-footer button[type=submit]').click();await s.page.waitForTimeout(250);
  assert.equal(await s.page.locator('#contact-name').inputValue(),'Fixture contact');assert.equal(await s.page.locator('.ui-modal [role=alert]').count(),1);assert.equal(await s.page.locator('.ui-modal-footer button[type=submit]').isDisabled(),false);
 },{override:async(r,u,q)=>{if(u.pathname.endsWith('/api/contacts')&&q.method()==='POST'){await json(r,{message:'Fixture validation failure'},400);return true;}}});
 await check('contact deletion requires confirmation and cancel sends no delete',async s=>{
  await s.goto('contacts');await s.page.locator('main button[aria-label=Delete]').first().click();await s.page.getByRole('dialog',{name:'Delete contact?'}).waitFor();await s.page.getByRole('button',{name:'Cancel',exact:true}).click();
  assert.equal(s.requests.filter(r=>r.method==='DELETE').length,0);
 });
 for(const noun of['users','groups'])await check(noun+' modal has keyboard containment and invalid submit sends no write',async s=>{
  await s.goto(noun);await s.page.locator('.panel-header-actions .btn-primary').click();await s.page.waitForTimeout(200);
  for(let i=0;i<12;i++){await s.page.keyboard.press('Tab');assert.equal(await s.page.getByRole('dialog').evaluate(e=>e.contains(document.activeElement)),true);}
  await s.page.locator('.ui-modal-footer button[type=submit]').click();assert.equal(await s.page.locator('.ui-modal [role=alert]').count(),1);assert.equal(s.requests.filter(r=>r.method==='POST').length,0);
  await s.page.keyboard.press('Escape');await s.page.waitForTimeout(250);assert.equal(await s.page.getByRole('dialog').count(),0);
 });
 await check('all five analytics reports open with a named current panel',async s=>{
  for(const tab of['overview','queues','agents','adherence','drilldown']){await s.goto('analytics?tab='+tab);assert.equal(await s.page.locator('#tab-'+tab).getAttribute('aria-selected'),'true');assert.equal(await s.page.locator('#tabpanel-'+tab).getAttribute('role'),'tabpanel');}
 });
 await check('recordings load failure offers a safe retry',async s=>{
  await s.goto('recordings');assert.equal(await s.page.getByRole('button',{name:'Retry',exact:true}).count(),1);assert.equal(await s.page.locator('main .ui-empty').count(),0);
 },{override:async(r,u)=>{if(u.pathname.endsWith('/api/recordings')){await json(r,{message:'Fixture unavailable'},503);return true;}}});
 await check('all log sub-tabs navigate without starting AMI capture',async s=>{
  for(const tab of['system','deliveries','audit']){await s.goto('logs?tab='+tab);assert.equal(await s.page.locator('#tab-'+tab).getAttribute('aria-selected'),'true');}
  assert.equal(s.requests.filter(r=>r.method==='POST').length,0);
 });
 await check('settings search and desktop section navigation preserve direct links',async s=>{
  await s.goto('settings');await s.page.locator('main input[type=search]').fill('record');await s.page.waitForTimeout(200);assert.ok(await s.page.locator('.sh-card').count()>0);assert.ok(await s.page.locator('.sh-card').count()<8);
  await s.goto('settings?tab=recording');await s.page.locator('.settings-section-nav button').filter({hasText:'Quality of Service'}).click();assert.ok(s.page.url().endsWith('settings?tab=qos'));
 });
 await check('mobile settings section picker works in Persian RTL',async s=>{
  await s.goto('settings?tab=recording');await s.page.locator('.settings-section-select select').selectOption('analytics');await s.page.locator('#an-sla').waitFor();assert.ok(s.page.url().endsWith('settings?tab=analytics'));
 },{lang:'fa',width:390});
 await check('supervisor API-key deep link explains the permission boundary',async s=>{
  await s.goto('settings?tab=api-keys');assert.equal(await s.page.locator('main [role=alert]').count(),1);assert.equal(await s.page.locator('main .btn-primary').count(),0);
 },{role:'supervisor'});
 await check('profile rejects mismatched passwords without a request',async s=>{
  await s.goto('profile');await s.page.locator('input[id$="-current"]').fill('FixtureOnlyCurrent');await s.page.locator('input[id$="-new"]').fill('FixtureOnlyNext42');await s.page.locator('input[id$="-confirm"]').fill('DifferentFixture42');
  assert.equal(await s.page.locator('.acct-form button[type=submit]').isDisabled(),true);assert.equal(s.requests.filter(r=>r.method==='POST').length,0);
 });
 await check('VAD checking and ready are based on the capability response',async s=>{
  const navigation=s.page.goto(app.origin+'/MytellDesk/settings?tab=recording',{waitUntil:'domcontentloaded'});await navigation;
  await s.page.locator('[data-vad-state=checking]').waitFor();await s.page.locator('[data-vad-state=ready]').waitFor();assert.equal(await s.page.locator('[data-vad-state=unavailable]').count(),0);
 },{override:async(r,u)=>{if(u.pathname.endsWith('/api/vad/status')){await new Promise(resolve=>setTimeout(resolve,1000));await json(r,{available:true,engine:'silero',error_code:null});return true;}}});
 await check('empty contact list offers an add action',async s=>{await s.goto('contacts');assert.equal(await s.page.locator('.ui-empty-action button').count(),1);},{mode:'empty'});
 await check('contact loading is announced before records arrive',async s=>{
  await s.page.goto(app.origin+'/MytellDesk/contacts',{waitUntil:'domcontentloaded'});await s.page.locator('.ui-loading[role=status]').waitFor();await s.page.locator('main tbody tr').first().waitFor();
 },{override:async(r,u)=>{if(u.pathname.endsWith('/api/contacts')){await new Promise(resolve=>setTimeout(resolve,700));await json(r,{contacts,next_cursor:null});return true;}}});
 console.log(JSON.stringify({passed:results.cases.filter(x=>x.pass).length,total:results.cases.length,out}));process.exitCode=results.cases.some(x=>!x.pass)?1:0;
})().catch(e=>{results.setupError=e.stack;console.error(e);process.exitCode=2;}).finally(async()=>{persist();await app?.close();});
