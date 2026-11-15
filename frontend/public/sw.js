/**
 * OpDesk service worker.
 *
 * Two jobs:
 *  1. Incoming-call notifications while the tab is backgrounded but still alive
 *     (registration.showNotification(), driven by the app: mobile Chrome forbids
 *     `new Notification()`, so the app goes through this registration).
 *  2. Web Push (VAPID): wake a backgrounded or closed tab for an incoming call. Browser
 *     push is userVisibleOnly, so it cannot silently answer the way a native app can; it
 *     shows a call notification the user taps to open OpDesk, which then re-registers
 *     SIP over WSS so the INVITE can reach it.
 *
 * Messages posted to the app (App.tsx listens):
 *  - {type:'opdesk:incoming-call', payload}       a push arrived while a tab is alive
 *  - {type:'opdesk:notification-action', action, payload}   the user tapped a notification
 *    ('answer' for the Answer action, 'open' otherwise)
 *  - {type:'opdesk:resubscribe'}                  the push subscription was rotated
 */

// Activate immediately so notifications work on the first load without a reload.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

const CALL_TAG = 'opdesk-incoming-call';
const APP_URL = new URL(self.registration.scope);
const ICON = new URL('favicon.svg', APP_URL).href;

// A panel or another application on this PBX is not a desk notification target.
async function appClients() {
  const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  return clients.filter((client) => {
    try {
      const url = new URL(client.url);
      return url.origin === APP_URL.origin &&
        (url.pathname === APP_URL.pathname.replace(/\/$/, '') || url.pathname.startsWith(APP_URL.pathname));
    } catch (_error) { return false; }
  });
}

// --- Web Push: incoming-call wake + banner alerts ---------------------------
self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (_e) {
    data = { type: 'alert', title: 'OpDesk', body: event.data ? event.data.text() : '' };
  }

  if (data.type === 'incoming_call') {
    const caller = data.display_name || data.caller || '';
    event.waitUntil((async () => {
      const clientList = await appClients();
      for (const client of clientList) {
        client.postMessage({ type: 'opdesk:incoming-call', payload: data });
      }
      // A visible app tab drives the ring UI itself; skip a duplicate OS notification.
      if (clientList.some((c) => c.visibilityState === 'visible')) return;
      await self.registration.showNotification(caller || 'Incoming call', {
        body: caller ? 'Incoming call' : '',
        tag: data.call_id || CALL_TAG,
        renotify: true,
        requireInteraction: true,
        icon: ICON,
        badge: ICON,
        data,
        actions: [
          { action: 'answer', title: 'Answer' },
          { action: 'decline', title: 'Decline' },
        ],
      });
    })());
    return;
  }

  // Generic banner (missed call, etc.)
  event.waitUntil((async () => {
    const clientList = await appClients();
    for (const client of clientList) {
      client.postMessage({ type: 'opdesk:incoming-call', payload: data });
    }
    await self.registration.showNotification(data.title || 'OpDesk', {
      body: data.body || '',
      icon: ICON,
      badge: ICON,
      data,
    });
  })());
});

// Rotated subscription — ask an open tab to re-subscribe and re-register (the
// re-subscribe endpoint needs the signed-in session, which only the app has).
self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil((async () => {
    const clients = await appClients();
    for (const client of clients) {
      client.postMessage({ type: 'opdesk:resubscribe' });
    }
  })());
});

// Tapping a notification (or its Answer action) focuses an existing app tab, relaying the
// tap so the app re-registers SIP, or opens one. Decline just dismisses it.
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  if (event.action === 'decline') return;

  event.waitUntil((async () => {
    const clientList = await appClients();
    const action = event.action === 'answer' ? 'answer' : 'open';
    for (const client of clientList) {
      if ('focus' in client) {
        client.postMessage({ type: 'opdesk:notification-action', action, payload: data });
        return client.focus();
      }
    }
    if (self.clients.openWindow) return self.clients.openWindow(APP_URL.href);
  })());
});
