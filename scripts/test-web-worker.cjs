/* Isolated service-worker events. No browser, push server or telephone is used. */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../frontend/public/sw.js'), 'utf8');
const scope = 'https://desk.example/MytellDesk/';

function client(url, visibilityState = 'hidden') {
  return {url, visibilityState, messages:[], focused:0,
    postMessage(message) { this.messages.push(message); },
    focus() { this.focused++; return Promise.resolve(this); }};
}
function fixture(clients = []) {
  const handlers = {}, notifications = [], opened = [];
  const self = {
    location: new URL('sw.js', scope),
    registration:{scope, showNotification(title, options) { notifications.push({title, options}); return Promise.resolve(); }},
    clients:{matchAll:async()=>clients, claim:async()=>{}, openWindow:async url=>opened.push(url)},
    addEventListener(type, handler) {handlers[type]=handler;}, skipWaiting:async()=>{},
  };
  vm.runInNewContext(source, {self, URL, console}, {filename:'sw.js'});
  async function dispatch(type, extra = {}) {
    const pending = [];
    handlers[type]({...extra, waitUntil(promise) {pending.push(promise);}});
    await Promise.all(pending);
  }
  return {notifications, opened, dispatch};
}
const incoming = {data:{json:()=>({type:'incoming_call',caller:'fixture caller',call_id:'fixture-call'})}};

test('a visible Issabel tab cannot suppress a background desk notification', async()=>{
  const panel=client('https://desk.example/index.php','visible'),desk=client(scope+'extensions');
  const f=fixture([panel,desk]); await f.dispatch('push',incoming);
  assert.equal(f.notifications.length,1);
  assert.equal(panel.messages.length,0);
  assert.equal(desk.messages.length,1);
});
test('notification icon and badge stay inside the desk application',async()=>{
  const f=fixture();await f.dispatch('push',incoming);
  assert.equal(new URL(f.notifications[0].options.icon,scope).href,scope+'favicon.svg');
  assert.equal(new URL(f.notifications[0].options.badge,scope).href,scope+'favicon.svg');
});
test('notification click focuses the desk instead of an earlier panel client',async()=>{
  const panel=client('https://desk.example/','visible'),desk=client(scope+'calls');
  const f=fixture([panel,desk]);await f.dispatch('notificationclick',{action:'answer',notification:{data:{type:'incoming_call'},close(){}}});
  assert.equal(panel.focused,0);assert.equal(panel.messages.length,0);assert.equal(desk.focused,1);
  assert.equal(desk.messages[0].action,'answer');
});
test('notification click opens the desk base when no desk client exists',async()=>{
  const f=fixture([client('https://desk.example/index.php')]);
  await f.dispatch('notificationclick',{notification:{data:{},close(){}}});
  assert.deepEqual(f.opened,[scope]);
});
test('similar paths and other origins are not desk clients',async()=>{
  const wrong=[client('https://desk.example/MytellDesk-old/','visible'),client('https://other.example/MytellDesk/','visible')];
  const f=fixture(wrong);await f.dispatch('push',incoming);
  assert.equal(f.notifications.length,1);assert.ok(wrong.every(c=>c.messages.length===0));
});
test('a visible desk client still suppresses a duplicate incoming banner',async()=>{
  const desk=client(scope,'visible'),f=fixture([desk]);await f.dispatch('push',incoming);
  assert.equal(f.notifications.length,0);assert.equal(desk.messages.length,1);
});
test('subscription rotation reaches only desk clients',async()=>{
  const panel=client('https://desk.example/'),desk=client(scope+'profile');
  const f=fixture([panel,desk]);await f.dispatch('pushsubscriptionchange');
  assert.equal(panel.messages.length,0);assert.equal(desk.messages[0].type,'opdesk:resubscribe');
});
test('generic alerts do not disclose their payload to unrelated clients',async()=>{
  const panel=client('https://desk.example/'),desk=client(scope);
  const f=fixture([panel,desk]);await f.dispatch('push',{data:{json:()=>({type:'alert',body:'fixture'})}});
  assert.equal(panel.messages.length,0);assert.equal(desk.messages.length,1);assert.equal(f.notifications.length,1);
});
test('declining a notification only dismisses it',async()=>{
  const desk=client(scope),f=fixture([desk]);let closed=0;
  await f.dispatch('notificationclick',{action:'decline',notification:{data:{},close(){closed++;}}});
  assert.equal(closed,1);assert.equal(desk.focused,0);assert.equal(f.opened.length,0);
});
