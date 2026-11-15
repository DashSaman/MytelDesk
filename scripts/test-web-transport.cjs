/* Actual TypeScript transport bundled in memory; every fetch is a local stub. */
'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const path=require('node:path'),vm=require('node:vm');
const {createRequire}=require('node:module');
const frontend=path.resolve(__dirname,'../frontend');
const requireFrontend=createRequire(path.join(frontend,'package.json'));
const esbuild=requireFrontend('esbuild');
const origin='https://desk.example';
function fixture(entry='src/lib/api.ts',response=()=>new Response('{"data":{"ok":true}}',{headers:{'Content-Type':'application/json'}})){
  const bundle=esbuild.buildSync({entryPoints:[path.join(frontend,entry)],bundle:true,write:false,format:'cjs',platform:'browser',define:{'import.meta.env':'{"BASE_URL":"/MytellDesk/","DEV":false}'}}).outputFiles[0].text;
  const data=new Map([['opdesk_token','fixture-token']]), requests=[],events=[];
  const module={exports:{}};
  const context={module,exports:module.exports,console,URL,Request,Response,Headers,FormData,DOMException,Event,
    location:new URL(origin+'/MytellDesk/'), navigator:{onLine:true},
    window:{location:new URL(origin+'/MytellDesk/'),dispatchEvent:e=>events.push(e.type)},
    localStorage:{getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)},
    fetch:async(input,init)=>{requests.push({input,init});return response(input,init);}};
  vm.runInNewContext(bundle,context,{filename:entry});
  return {api:module.exports,requests,data,events,context};
}
function target(call){return call.input instanceof Request?call.input.url:String(call.input);}
test('fetchWithAuth prefixes a root API path and preserves its query',async()=>{
 const f=fixture('src/auth.ts');await f.api.fetchWithAuth('/api/auth/me?fixture=1');
 assert.equal(new URL(target(f.requests[0]),origin).pathname,'/MytellDesk/api/auth/me');
 assert.equal(new URL(target(f.requests[0]),origin).search,'?fixture=1');
});
test('already based API paths are not double-prefixed',async()=>{
 const f=fixture('src/auth.ts');await f.api.fetchWithAuth('/MytellDesk/api/auth/me');
 assert.equal(new URL(target(f.requests[0]),origin).pathname,'/MytellDesk/api/auth/me');
});
test('relative API paths resolve at the application base rather than the active route',async()=>{
 const f=fixture('src/auth.ts');f.context.window.location=new URL(origin+'/MytellDesk/settings/');
 await f.api.fetchWithAuth('api/fixture?one=two');
 assert.equal(new URL(target(f.requests[0]),f.context.window.location).href,origin+'/MytellDesk/api/fixture?one=two');
});
test('URL input uses the same application API base',async()=>{
 const f=fixture('src/auth.ts');await f.api.fetchWithAuth(new URL(origin+'/api/fixture'));
 assert.equal(new URL(target(f.requests[0]),origin).pathname,'/MytellDesk/api/fixture');
});
test('Request input preserves method, body, signal and headers when rebased',async()=>{
 const f=fixture('src/auth.ts'),abort=new AbortController();
 const request=new Request(origin+'/api/fixture',{method:'POST',body:'fixture=one',headers:{'X-Fixture':'retained'},signal:abort.signal});
 await f.api.fetchWithAuth(request);
 const call=f.requests[0],sent=new Request(new URL(target(call),origin),call.input instanceof Request?call.input:undefined);
 const effective=new Request(sent,call.init);
 assert.equal(new URL(effective.url).pathname,'/MytellDesk/api/fixture');assert.equal(effective.method,'POST');
 assert.equal(await effective.text(),'fixture=one');assert.equal(effective.headers.get('X-Fixture'),'retained');
 assert.equal(effective.headers.get('Authorization'),'Bearer fixture-token');abort.abort();assert.equal(effective.signal.aborted,true);
});
test('Headers instances keep caller headers',async()=>{
 const f=fixture('src/auth.ts');await f.api.fetchWithAuth('/api/fixture',{headers:new Headers({'X-Fixture':'retained'})});
 const headers=new Headers(f.requests[0].init.headers);assert.equal(headers.get('X-Fixture'),'retained');
 assert.equal(headers.get('Authorization'),'Bearer fixture-token');
});
test('central API preserves based success envelopes',async()=>{
 const f=fixture();assert.equal((await f.api.api('/api/fixture')).ok,true);
 assert.equal(target(f.requests[0]),'/MytellDesk/api/fixture');
});
test('central API does not double-prefix an already based path',async()=>{
 const f=fixture();await f.api.api('/MytellDesk/api/fixture');assert.equal(target(f.requests[0]),'/MytellDesk/api/fixture');
});
test('central API accepts Headers instances without losing caller headers',async()=>{
 const f=fixture();await f.api.api('/api/fixture',{headers:new Headers({'X-Fixture':'retained'})});
 const headers=new Headers(f.requests[0].init.headers);assert.equal(headers.get('X-Fixture'),'retained');
 assert.equal(headers.get('Authorization'),'Bearer fixture-token');
});
for(const [name,type,body] of [['HTML','text/html','<html>proxy page</html>'],['malformed JSON','application/json','{broken']]){
 test('nonempty successful '+name+' is an explicit malformed response',async()=>{
  const f=fixture(undefined,()=>new Response(body,{status:200,headers:{'Content-Type':type}}));
  await assert.rejects(f.api.api('/api/fixture'),e=>e.code==='client.response.malformed');
 });
}
test('204 remains a legitimate empty success',async()=>{
 const f=fixture(undefined,()=>new Response(null,{status:204}));assert.equal(await f.api.api('/api/fixture'),null);
});
test('empty successful body remains legitimate without content-length',async()=>{
 const f=fixture(undefined,()=>new Response('',{status:200}));assert.equal(await f.api.api('/api/fixture'),null);
});
test('valid JSON null remains valid',async()=>{
 const f=fixture(undefined,()=>new Response('null',{headers:{'Content-Type':'application/json'}}));assert.equal(await f.api.api('/api/fixture'),null);
});
test('401 preserves authored error and clears local authentication',async()=>{
 const f=fixture(undefined,()=>new Response('{"error":{"code":"auth.expired","message":"expired","retryable":false}}',{status:401,headers:{'Content-Type':'application/json'}}));
 await assert.rejects(f.api.api('/api/fixture'),e=>e.code==='auth.expired');assert.equal(f.data.has('opdesk_token'),false);assert.deepEqual(f.events,['opdesk:unauthorized']);
});
test('HTML proxy error stays retryable with no automatic resend',async()=>{
 const f=fixture(undefined,()=>new Response('proxy failure',{status:503,headers:{'Content-Type':'text/html'}}));
 await assert.rejects(f.api.api('/api/fixture'),e=>e.code==='client.response.malformed'&&e.retryable===true);assert.equal(f.requests.length,1);
});
test('aborted request remains distinguishable and is not retried',async()=>{
 const f=fixture(undefined,()=>{throw new DOMException('cancelled','AbortError');});
 await assert.rejects(f.api.api('/api/fixture'),e=>e.code==='client.request.aborted');assert.equal(f.requests.length,1);
});
