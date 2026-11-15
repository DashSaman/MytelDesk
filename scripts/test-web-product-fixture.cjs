/* Actual React product, deterministic synthetic API/WS data, loopback network only. */
'use strict';
const fs=require('node:fs'),path=require('node:path');
const {createRequire}=require('node:module'),{pathToFileURL}=require('node:url');
const {chromium}=require('playwright');
const frontend=path.resolve(__dirname,'../frontend');
const requireFrontend=createRequire(path.join(frontend,'package.json'));
const user={id:1,username:'demo-admin',name:'مدیر مرکز تماس',role:'admin',extension:null,is_active:1,monitor_modes:['listen','whisper','barge']};
const stamp='2026-11-15T09:15:00+03:30';
const contacts=[
 {id:1,name:'شرکت آفتاب',phone:'02100001001',company:'خدمات ارتباطی آفتاب',notes:'هماهنگی پشتیبانی شعبه مرکزی',source:'manual'},
 {id:2,name:'گروه سپهر',phone:'02100001002',company:'فناوری سپهر',notes:'پیگیری سفارش تجهیزات',source:'crm'},
 {id:3,name:'مریم احمدی',phone:'09120001003',company:'فروشگاه نمونه',notes:'',source:'api'},
];
const extensions=Object.fromEntries(['201','202','203','204','205','206'].map((extension,i)=>[extension,{extension,name:['پشتیبانی — سارا','فروش — رضا','امور مشتریان','حسابداری','دفتر مدیریت','پشتیبانی شعبه'][i],status:['idle','in_call','ringing','on_hold','unavailable','idle'][i],status_code:String([1,2,6,16,4,1][i]),call_info:null}]));
const call={extension:'202',state:'Up',talking_to:'02100001001',contact_name:'شرکت آفتاب',duration:'00:02:18',talk_time:'00:02:05',channel:'PJSIP/202-fixture',caller:'202',callerid:'رضا',destination:'02100001001',original_destination:'02100001001',route:'outbound'};
extensions['202'].call_info=call;
const queues={'600':{extension:'600',name:'پشتیبانی مشتریان',members:{},calls_waiting:1,sla_secs:20},'601':{extension:'601',name:'فروش و سفارش‌ها',members:{},calls_waiting:0,sla_secs:30}};
const members=Object.fromEntries(['201','202','206'].map((ext,i)=>[ext,{queue:i===2?'601':'600',interface:'PJSIP/'+ext,membername:extensions[ext].name,status:i===1?'2':'1',paused:false,dynamic:true}]));
const state={extensions,active_calls:{fixture:call},queues,queue_members:members,queue_entries:{fixture:{queue:'600',callerid:'02100001003',position:1,wait_time:'00:00:35'}},agent_states:{},stats:{total_extensions:6,online_extensions:5,total_queues:2,total_waiting:1,active_calls_count:1}};
const emptyState={extensions:{},active_calls:{},queues:{},queue_members:{},queue_entries:{},agent_states:{},stats:{total_extensions:0,online_extensions:0,total_queues:0,total_waiting:0,active_calls_count:0}};
const agents=Object.values(extensions).map(x=>({extension:x.extension,name:x.name}));
const groups=[{id:1,name:'پشتیبانی',agent_extensions:['201','202'],queues:[{extension:'600',queue_name:'پشتیبانی مشتریان'}],user_ids:[1,2]}];
const users=[user,{id:2,username:'sara',name:'سارا رضایی',extension:'201',role:'agent',is_active:1,group_ids:[1],agent_extensions:['201'],queue_names:['600'],monitor_modes:[]}];
const calls=[0,1,2].map(i=>({calldate:stamp,src:'0210000100'+(i+1),dst:i===1?'202':'600',phone_number:'0210000100'+(i+1),customer_name:contacts[i].name,duration:138-i*31,talk:i===2?0:125-i*31,disposition:i===2?'NO ANSWER':'ANSWERED',status:i===2?'UNANSWERED':'ANSWERED',QoS:null,extension:i===2?null:'201',agent_name:'سارا',call_type:'IN',route:'inbound_queue',recording_path:null,recording_file:null,app:'Queue',ended_by:'customer',linkedid:'fixture-'+i,uniqueid:'fixture-'+i}));
const kpis={total_calls:86,answered_calls:78,abandoned_calls:8,abandonment_rate:9.3,sla_pct:89,aht_secs:126,avg_wait_secs:16,fcr_pct:82,customers:68,customers_answered:63,answer_rate:92.6,contact_rate:90.7,abandoned_callers:6,recovered_callers:4,recovery_rate:66.7,net_abandonment_rate:2.3,outbound:{total_calls:32,answered_calls:25,no_answer_calls:4,busy_calls:2,failed_calls:1,customers:27,customers_answered:23,answer_rate:85.2,contact_rate:78.1,aht_secs:99,sum_billsec:2475},combined:{total_calls:128,answered_calls:112,customers:91,customers_answered:86,answer_rate:94.5,contact_rate:87.5,aht_secs:119,internal_calls:10}};
const analyticsSettings={sla_thresholds:{},sla_default_secs:20,sla_target_pct:80,short_abandon_secs:5,fcr_window_days:7,callback_window_hours:24};
const policy={enabled:true,format:'wav',engine:'issabel',directions:{inbound:true,outbound:true,internal:false},extensions:agents.map(x=>({...x,recording:true})),hook_conflicts:[]};
const push={mobile_wake:{enabled:false,hold_seconds:10,settle_seconds:3,client_marker:'Medut'},fcm:{project_id:'',has_service_account:false,service_account_email:'',service_account_project:'',source:null,ready:false},apns:{key_id:'',team_id:'',bundle_id:'',has_auth_key:false,source:null,sandbox:false,ready:false},web:{public_key:'',ready:false,library_installed:true},bindings:[]};
function payload(url,mode='data'){
 const p=url.pathname.replace(/^\/MytellDesk/,''); const empty=mode==='empty';
 const entries=empty?[]:contacts;
 const map={
 '/api/auth/me':user,'/api/webrtc/config':{server:'',extension:null,extension_secret:null},
 '/api/settings/extensions/webrtc':{extensions:agents.map(x=>({...x,webrtc:'no'}))},
 '/api/call-notifications':{notifications:[],total:0},'/api/push/config':push,
 '/api/settings/users':{users:empty?[]:users},'/api/settings/groups':{groups:empty?[]:groups},'/api/settings/agents':{agents},'/api/settings/queues':{queues:Object.values(queues).map(q=>({extension:q.extension,queue_name:q.name}))},
 '/api/contacts':{contacts:entries.filter(x=>!url.searchParams.get('search')||x.name.includes(url.searchParams.get('search'))),next_cursor:null},
 '/api/call-log':{calls:empty?[]:calls,total:empty?0:calls.length},
 '/api/recordings':{recordings:empty?[]:calls.map(c=>({...c,billsec:c.talk,recordingfile:c.uniqueid+'.wav',size_bytes:204800})),total:empty?0:3,next_cursor:null},
 '/api/analytics/overview':{period:{date_from:'2026-10-16',date_to:'2026-11-15'},current:kpis,prev_period:null,settings:analyticsSettings},
 '/api/analytics/trend':{trend:empty?[]:Array.from({length:7},(_,i)=>({date:'2026-11-'+(9+i),total_calls:20+i*3,answered_calls:18+i*3,abandoned_calls:2,outbound_total:10+i,outbound_answered:8+i})),hourly:[]},
 '/api/analytics/queues':{queues:[]},'/api/analytics/agents':{agents:[]},'/api/analytics/agent-adherence':{agents:[],rows:[]},'/api/analytics/drilldown':{calls:[],records:[],total:0,facets:{}},
 '/api/analytics/settings':analyticsSettings,
 '/api/api-keys':{api_keys:[]},'/api/api-keys/permissions':{permissions:[],scopes:[]},
 '/api/pause-reasons':{reasons:[]},'/api/agents/wrapup-config':{max_secs:120,min_secs:0,max_secs_limit:1800},'/api/agents/shift-reset':{time:'04:00'},
 '/api/qos/status':{enabled:true},'/api/sip-tls/status':{enabled:true,domain:'pbx.example.test',udp_tcp_public:false},
 '/api/recording/status':{enabled:true,format:'wav'},'/api/recording/policy':policy,
 '/api/recording/retention':{local_days:30,local_max_bytes:null,vad_delete_legs:true,root_dir:'/var/spool/asterisk/monitor'},
 '/api/recording/storage-stats':{total_recordings:128,local:{used_bytes:1024**3,max_bytes:null,disk_free_bytes:20*1024**3,disk_total_bytes:50*1024**3,retention_days:30}},
 '/api/crm/config':{enabled:false,sync_enabled:false,lookup_enabled:false,url:'',method:'POST',headers:{},body_fields:[],field_map:{},field_catalog:[],status_map:{}},
 '/api/logs/ami':{enabled:false},'/api/logs/siptrace':{enabled:false},'/api/logs/events':{events:[],counts:{}},'/api/logs':{lines:[],cursor:0},'/api/logs/deliveries':{deliveries:[],total:0,facets:{}},'/api/audit-log':{entries:[],rows:[],total:0},
 };
 return map[p]??{};
}
async function start(){
 const vite=await import(pathToFileURL(requireFrontend.resolve('vite')).href);
 const react=(await import(pathToFileURL(requireFrontend.resolve('@vitejs/plugin-react')).href)).default;
 const server=await vite.createServer({root:frontend,configFile:false,base:'/MytellDesk/',plugins:[react()],logLevel:'error',server:{host:'127.0.0.1',port:0,hmr:false}});
 await server.listen();const origin='http://127.0.0.1:'+server.httpServer.address().port;
 const browser=await chromium.launch({headless:true,channel:process.env.MT_TEST_BROWSER||'chrome',args:['--mute-audio']});
 async function session({width=1440,height=1000,theme='light',lang='fa',mode='data',role='admin',override}={}){
  const context=await browser.newContext({viewport:{width,height},serviceWorkers:'block',locale:'en-US',timezoneId:'Asia/Tehran'});
  const requests=[],errors=[],unknown=[];const me={...user,role};
  await context.addInitScript(({me,theme,lang})=>{
   localStorage.setItem('opdesk_token','product-fixture');localStorage.setItem('opdesk_user',JSON.stringify(me));localStorage.setItem('opdesk-lang',lang);localStorage.setItem('opdesk:theme',theme);
   HTMLMediaElement.prototype.play=async()=>{};
   if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=async()=>{throw new Error('No live media in product fixture');};
   if('Notification'in window)Object.defineProperty(Notification,'permission',{get:()=>'denied'});
   if('serviceWorker'in navigator)navigator.serviceWorker.register=async()=>{throw new Error('Worker activation disabled in fixture');};
  },{me,theme,lang});
  await context.routeWebSocket('**/*',socket=>{
   if(socket.url()!==origin.replace(/^http/,'ws')+'/MytellDesk/ws')return socket.close();
   socket.onMessage(message=>{
    const data=JSON.parse(String(message));
    if(data.token==='product-fixture')socket.send(JSON.stringify({type:'initial_state',data:mode==='empty'?emptyState:state}));
    else {requests.push({method:'WS',path:'action',action:data.action});socket.send(JSON.stringify({type:'action_result',success:true,action:data.action,message:'Fixture only'}));}
   });
  });
  await context.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.origin!==origin){errors.push('External request blocked: '+url.origin);return route.abort();}
   if(url.pathname.startsWith('/MytellDesk/api/')){
    requests.push({method:request.method(),path:url.pathname,search:url.search});
    const p=url.pathname.replace(/^\/MytellDesk/,'');
    if(override&&await override(route,url,request))return;
    if(p==='/api/codecs')return route.fulfill({status:404,contentType:'application/json',body:'{"message":"Unsupported on this PBX"}'});
    let data=p==='/api/auth/me'?me:payload(url,mode);
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
   }
   if(!['GET','HEAD','OPTIONS'].includes(request.method())){errors.push('Unexpected local request');return route.abort();}
   return route.continue();
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  async function goto(route){await page.goto(origin+'/MytellDesk/'+route,{waitUntil:'networkidle',timeout:45000});await page.locator('main').waitFor();await page.waitForTimeout(120);}
  return{context,page,goto,requests,errors,unknown,close:()=>context.close()};
 }
 return{browser,origin,session,close:async()=>{await browser.close();await server.close();}};
}
module.exports={start,payload,user,contacts,state,emptyState};
