{% load static %}
const APP_CACHE_PREFIX = "applacolina-pwa";
const APP_CACHE_VERSION = "v2";
const STATIC_CACHE = `${APP_CACHE_PREFIX}-${APP_CACHE_VERSION}-static`;
const PRECACHE_URLS = [
  "/task-manager/telegram/mini-app/",
  "/task-manager/telegram/mini-app/?utm_source=pwa",
  "{% static 'task_manager/pwa/offline.html' %}"
];
const OFFLINE_FALLBACK_URL = "{% static 'task_manager/pwa/offline.html' %}";
const DEFAULT_NOTIFICATION_ICON = "{% static 'task_manager/pwa/icons/icon-192.png' %}";
const DEFAULT_NOTIFICATION_BADGE = "{% static 'task_manager/pwa/icons/icon-96.png' %}";
const START_URL = "/task-manager/telegram/mini-app/?utm_source=pwa";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE_URLS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key.startsWith(APP_CACHE_PREFIX) && key !== STATIC_CACHE)
          .map((key) => caches.delete(key))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);
  const isStaticAsset = url.pathname.startsWith("/static/");
  const isScriptRequest =
    isStaticAsset && (request.destination === "script" || url.pathname.endsWith(".js"));

  // Allow cross-origin requests to fail fast without cache.
  if (url.origin !== location.origin) {
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_FALLBACK_URL)));
    return;
  }

  // Network-first for dynamic views (task manager).
  if (url.pathname.startsWith("/task-manager/")) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(STATIC_CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => caches.match(request).then((cached) => cached || caches.match(OFFLINE_FALLBACK_URL)))
    );
    return;
  }

  // Network-first for JS bundles to avoid stale code.
  if (isScriptRequest) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(STATIC_CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() =>
          caches.match(request).then((cached) => cached || caches.match(OFFLINE_FALLBACK_URL))
        )
    );
    return;
  }

  // Cache-first for other static assets.
  if (isStaticAsset) {
    event.respondWith(
      caches.match(request).then((cachedResponse) => {
        if (cachedResponse) {
          return cachedResponse;
        }
        return fetch(request)
          .then((response) => {
            if (response.ok) {
              const copy = response.clone();
              caches.open(STATIC_CACHE).then((cache) => cache.put(request, copy));
            }
            return response;
          })
          .catch(() => caches.match(OFFLINE_FALLBACK_URL));
      })
    );
    return;
  }

  // Default: try cache, then network, finally offline fallback.
  event.respondWith(
    caches.match(request).then((cachedResponse) => {
      if (cachedResponse) {
        return cachedResponse;
      }
      return fetch(request).catch(() => caches.match(OFFLINE_FALLBACK_URL));
    })
  );
});

const buildNotificationOptions = (payload) => {
  const body = payload.body || "Tienes una nueva actualización en Granjas La Colina.";
  const icon = payload.icon || DEFAULT_NOTIFICATION_ICON;
  const badge = payload.badge || DEFAULT_NOTIFICATION_BADGE;
  const data = payload.data || {};
  const actions = Array.isArray(payload.actions) ? payload.actions : [];

  return {
    body,
    icon,
    badge,
    data: { ...data, url: data.url || START_URL },
    requireInteraction: payload.requireInteraction || false,
    vibrate: payload.vibrate || [150, 60, 150],
    tag: payload.tag || "applacolina-alert",
    renotify: payload.renotify || false,
    actions,
  };
};

self.addEventListener("push", (event) => {
  let payload = {};
  if (event.data) {
    try {
      payload = event.data.json();
    } catch (error) {
      payload = { body: event.data.text() };
    }
  }

  const title = payload.title || "Granjas La Colina";
  const options = buildNotificationOptions(payload);
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || START_URL;

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientsArr) => {
      const focusedClient = clientsArr.find((client) => client.url.includes("/task-manager/telegram/mini-app/"));
      if (focusedClient) {
        focusedClient.postMessage({ type: "PUSH_NAVIGATION", url: targetUrl });
        return focusedClient.focus();
      }
      return self.clients.openWindow(targetUrl);
    })
  );
});

self.addEventListener("pushsubscriptionchange", (event) => {
  const broadcastMessage = async (payload) => {
    const clientsList = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    clientsList.forEach((client) => client.postMessage(payload));
  };

  event.waitUntil(
    broadcastMessage({
      type: "PUSH_SUBSCRIPTION_CHANGED",
    })
  );
});
