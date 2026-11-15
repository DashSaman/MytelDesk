/* Civil-date conversion and actual product controls; all API responses are local fixtures. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),vm=require('node:vm');
const {createRequire}=require('node:module');
const {start}=require('./test-web-product-fixture.cjs');
const frontend=path.resolve(__dirname,'../frontend'),requireFrontend=createRequire(path.join(frontend,'package.json'));
const out=process.env.MT_WEB_OUTPUT||fs.mkdtempSync(path.join(os.tmpdir(),'myteldesk-calendar-'));fs.mkdirSync(out,{recursive:true});
const result={scope:'Jalali civil dates and native React range/expiry controls, synthetic APIs only',cases:[]};
const persist=()=>fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2)+'\n');
let app;
async function check(name,fn){try{await fn();result.cases.push({name,pass:true});console.log('PASS '+name);}catch(e){result.cases.push({name,pass:false,error:e.message});console.log('FAIL '+name+': '+e.message);}persist();}
function dates(){
 const code=requireFrontend('esbuild').buildSync({entryPoints:[path.join(frontend,'src/lib/persianDate.ts')],bundle:true,write:false,format:'cjs',logLevel:'silent'}).outputFiles[0].text;
 const module={exports:{}};vm.runInNewContext(code,{module,exports:module.exports,Date});return module.exports;
}
async function browserCase(name,fn,opts={}){await check(name,async()=>{const s=await app.session(opts);s.page.setDefaultTimeout(5000);try{await fn(s);assert.deepEqual(s.errors,[]);}finally{await s.close();}});}
async function openRange(s){await s.goto('call-log');await s.page.locator('.ui-range-trigger').click();return s.page.locator('.ui-range-custom');}
(async()=>{
 await check('known civil dates map to exact ISO days including Nowruz and leap Esfand',()=>{
  const d=dates();for(const[input,iso]of[['۱۳۹۹/۱۲/۳۰','2021-03-20'],['1403/12/30','2025-03-20'],['۱۴۰۴/۰۱/۰۱','2025-03-21'],['۱۴۰۵/۰۸/۲۴','2026-11-15']])assert.equal(d.parsePersianDate(input),iso);
 });
 await check('invalid, incomplete and out-of-range Persian days never normalize into a different date',()=>{
  const d=dates();for(const value of['1400/12/30','1405/07/31','1405/13/01','1405/00/01','1405/01/00','1405/01/32','1405/8','0000/1/1','9999/1/1','abc'])assert.equal(d.parsePersianDate(value),null,value);
 });
 await check('min and max bounds are inclusive and compare ISO civil dates',()=>{
  const d=dates();assert.equal(d.parsePersianDate('1405/08/24','2026-11-15','2026-11-15'),'2026-11-15');
  assert.equal(d.parsePersianDate('1405/08/23','2026-11-15'),null);assert.equal(d.parsePersianDate('1405/08/25',undefined,'2026-11-15'),null);
 });
 await check('Persian and Arabic numeral input share the ISO contract and blank means cleared',()=>{
  const d=dates();assert.equal(d.parsePersianDate('١٤٠٥/٠٨/٢٤'),'2026-11-15');assert.equal(d.parsePersianDate(''),'');assert.equal(d.formatPersianDate('2026-11-15'),'۱۴۰۵/۰۸/۲۴');
  assert.equal(d.formatPersianDate('2026-02-30'),'');assert.equal(d.formatPersianDate('invalid'),'');
 });
 await check('calendar day arithmetic crosses Gregorian DST, month and year boundaries without a clock',()=>{
  const d=dates();assert.equal(d.shiftCivilDate('2026-03-08',1),'2026-03-09');assert.equal(d.shiftCivilDate('2025-03-20',1),'2025-03-21');assert.equal(d.shiftCivilDate('2026-01-01',-1),'2025-12-31');
  assert.equal(d.persianMonthInfo(1403,12).days,30);assert.equal(d.persianMonthInfo(1404,12).days,29);assert.equal(d.persianMonthInfo(1405,8).offset,6);
 });
 app=await start();
 for(const timezoneId of['Asia/Tehran','America/Los_Angeles','Pacific/Auckland'])await browserCase('Persian range input retains its exact civil date in '+timezoneId,async s=>{
  const range=await openRange(s),input=range.locator('input').first();assert.equal(await input.getAttribute('type'),'text');
  await input.fill('۱۴۰۳/۱۲/۳۰');await input.blur();await s.page.waitForTimeout(450);
  assert.ok(s.requests.some(r=>r.path==='/MytellDesk/api/call-log'&&new URLSearchParams(r.search).get('date_from')==='2025-03-20'));
  assert.equal(await input.inputValue(),'۱۴۰۳/۱۲/۳۰');
 },{timezoneId});
 await browserCase('invalid range text is explained and sends no replacement query',async s=>{
  const range=await openRange(s),input=range.locator('input').first();const before=s.requests.filter(r=>r.path.endsWith('/call-log')).length;
  await input.fill('۱۴۰۰/۱۲/۳۰');await input.blur();await s.page.waitForTimeout(450);
  assert.equal(await input.getAttribute('aria-invalid'),'true');assert.equal(s.requests.filter(r=>r.path.endsWith('/call-log')).length,before);
  assert.ok(await range.locator('[role=alert]').count());
 });
 await browserCase('Persian calendar has a Saturday-first grid and keyboard selection keeps the range dialog open',async s=>{
  const range=await openRange(s);await range.locator('input').first().fill('۱۴۰۵/۰۸/۲۲');await range.locator('.ui-date-calendar-toggle').first().click();
  const cal=range.locator('.ui-date-calendar');assert.equal(await cal.locator('th').first().innerText(),'ش');
  const selected=cal.locator('[aria-pressed=true]');await selected.focus();await s.page.keyboard.press('ArrowLeft');await s.page.keyboard.press('Enter');
  assert.equal(await range.locator('input').first().inputValue(),'۱۴۰۵/۰۸/۲۳');assert.equal(await s.page.locator('.ui-popover').count(),1);
  assert.equal(await range.locator('.ui-date-calendar-toggle').first().evaluate(e=>e===document.activeElement),true);
 });
 await browserCase('calendar Escape closes the inner calendar and preserves its range picker',async s=>{
  const range=await openRange(s);await range.locator('.ui-date-calendar-toggle').first().click();await s.page.keyboard.press('Escape');
  assert.equal(await range.locator('.ui-date-calendar').count(),0);assert.equal(await s.page.locator('.ui-popover').count(),1);
 });
 await browserCase('English range retains native Gregorian inputs',async s=>{
  const range=await openRange(s);assert.equal(await range.locator('input[type=date]').count(),2);
  await range.locator('input').first().fill('2025-03-20');await s.page.waitForTimeout(450);
  assert.ok(s.requests.some(r=>new URLSearchParams(r.search).get('date_from')==='2025-03-20'));
 },{lang:'en'});
 let savedExpiry=null;
 await browserCase('API key expiry validates Persian drafts and posts the exact ISO civil date',async s=>{
  await s.goto('settings?tab=api-keys');await s.page.locator('main .ui-card-actions button').last().click();
  const input=s.page.locator('#ak-expires');assert.equal(await input.getAttribute('type'),'text');await input.fill('۱۴۰۰/۱۲/۳۰');await input.blur();
  assert.equal(await s.page.locator('.ui-modal-footer .btn-primary').isDisabled(),true);
  assert.equal(savedExpiry,null);
  await input.fill('۱۴۰۵/۰۹/۰۱');await input.blur();assert.equal(await input.getAttribute('aria-invalid'),'false');
  assert.equal(await s.page.locator('.ui-modal-footer .btn-primary').isDisabled(),false);
  await s.page.locator('#ak-name').fill('Fixture calendar key');await s.page.locator('.apikey-scope-grid input[type=checkbox]').first().check();
  await s.page.locator('.ui-modal-footer .btn-primary').click();await s.page.waitForTimeout(350);assert.equal(savedExpiry,'2026-11-22');
 },{override:async(route,url,request)=>{
  if(url.pathname==='/MytellDesk/api/api-keys/permissions'){await route.fulfill({contentType:'application/json',body:JSON.stringify({permissions:['calls:read']})});return true;}
  if(url.pathname==='/MytellDesk/api/api-keys'&&request.method()==='POST'){savedExpiry=JSON.parse(request.postData()).expires_at;await route.fulfill({contentType:'application/json',body:JSON.stringify({id:99,name:'Fixture calendar key',key:'fixture-not-a-real-key',scopes:['calls:read']})});return true;}
 }});
 for(const theme of['light','dark'])await browserCase('Persian mobile '+theme+' date picker stays inside the viewport',async s=>{
  const range=await openRange(s);await range.locator('.ui-date-calendar-toggle').first().click();
  assert.equal(await s.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  const r=await range.locator('.ui-date-calendar').boundingBox();assert.ok(r.x>=0&&r.x+r.width<=391);
  await s.page.screenshot({path:path.join(out,'calendar-'+theme+'.png')});
 },{width:390,theme});
 process.exitCode=result.cases.some(x=>!x.pass)?1:0;console.log(JSON.stringify({passed:result.cases.filter(x=>x.pass).length,total:result.cases.length,out}));
})().catch(e=>{result.setupError=e.message;process.exitCode=2;console.error(e);}).finally(async()=>{persist();await app?.close();});
