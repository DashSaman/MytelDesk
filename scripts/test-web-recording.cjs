/* Native players + authenticated blob/download lifecycle. Synthetic WAV only; no PBX. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {start,payload}=require('./test-web-product-fixture.cjs');
const out=path.resolve(process.env.MT_WEB_OUTPUT||fs.mkdtempSync(path.join(os.tmpdir(),'myteldesk-recording-')));fs.mkdirSync(out,{recursive:true});
const result={scope:'Actual React recording players; synthetic local WAV, API and auth; no real media/calls',cases:[]};
const recordingPath='/var/spool/asterisk/monitor/2026/11/15/تماس + #?%&= 100.wav';
const audio=Buffer.alloc(44+8000);audio.write('RIFF');audio.writeUInt32LE(audio.length-8,4);audio.write('WAVEfmt ',8);audio.writeUInt32LE(16,16);
audio.writeUInt16LE(1,20);audio.writeUInt16LE(1,22);audio.writeUInt32LE(8000,24);audio.writeUInt32LE(16000,28);
audio.writeUInt16LE(2,32);audio.writeUInt16LE(16,34);audio.write('data',36);audio.writeUInt32LE(audio.length-44,40);
let app;
async function check(name,routeName,status=200){
 const fileRequests=[];
 const s=await app.session({lang:'en',override:async(route,url,request)=>{
  const routePath=url.pathname.replace(/^\/MytellDesk/,'');
  if(routePath==='/api/recordings'||routePath==='/api/call-log'){
   const data=payload(url,'data'),key=routePath==='/api/recordings'?'recordings':'calls';
   data[key]=data[key].slice(0,1).map(row=>({...row,recording_path:recordingPath,recording_file:'fixture.wav',recordingfile:'fixture.wav'}));
   await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});return true;
  }
  if(routePath==='/api/recording-file'||routePath.startsWith('/api/recordings/')){
   fileRequests.push({path:url.pathname,query:url.searchParams.get('path'),auth:request.headers().authorization});
   // Model Apache's pathname rejection; the query is forwarded unchanged.
   const code=/%2f/i.test(url.pathname)?404:status;
   await route.fulfill({status:code,contentType:code===200?'audio/x-wav':'application/json',body:code===200?audio:'{}'});return true;
  }
 }});
 try{
  await s.goto(routeName);assert.equal(fileRequests.length,0,'audio must load only on request');
  await s.page.locator('.cl-rec').first().getByRole('button',{name:'Play',exact:true}).click();
  if(status===200){
   await s.page.waitForFunction(()=>{const audio=document.querySelector('main .cl-rec audio');return audio&&audio.src.startsWith('blob:')&&audio.duration>0;});
   assert.equal(fileRequests.length,1);assert.equal(fileRequests[0].path,'/MytellDesk/api/recording-file');
   assert.equal(fileRequests[0].query,recordingPath);assert.equal(fileRequests[0].auth,'Bearer product-fixture');
   const download=s.page.waitForEvent('download');await s.page.locator('.cl-rec').first().getByRole('button',{name:'Download',exact:true}).click();
   const file=await download;assert.equal(file.suggestedFilename(),'fixture.wav');
   assert.deepEqual(fs.readFileSync(await file.path()),audio);assert.equal(fileRequests.length,1,'download reuses the loaded authenticated blob');
  }else{
   await s.page.locator('main .cl-no-recording').first().waitFor();assert.equal(fileRequests.length,1);
   assert.equal(await s.page.locator('main .cl-rec').count(),0);
   const title=await s.page.locator('main .cl-no-recording').first().getAttribute('title');
   assert.match(title,status===403?/not allowed|access|permission/i:/no recording/i);
  }
  assert.deepEqual(s.errors,[]);result.cases.push({name,pass:true});console.log('PASS '+name);
 }catch(e){result.cases.push({name,pass:false,error:e.message});console.log('FAIL '+name+': '+e.message);}
 finally{await s.close();fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));}
}
(async()=>{app=await start();try{
 await check('Recordings player loads metadata and downloads exact authenticated WAV','recordings');
 await check('Call History player loads metadata and downloads exact authenticated WAV','call-log');
 await check('forbidden recording shows permission state without retry','recordings',403);
 await check('missing recording shows unavailable state without retry','recordings',404);
}finally{await app.close();}process.exitCode=result.cases.every(c=>c.pass)?0:1;})().catch(e=>{console.error(e);process.exitCode=1;});
