(function pwaBootstrap() {
  const defaultConfig = {
    debug: false,
    serviceWorkerUrl: "/service-worker.js",
    scope: "/",
    vapidPublicKey: null,
    subscriptionEndpoint: null,
    autoRegister: true,
  };

  const runtimeConfig = window.PWAPushConfig || {};
  const config = { ...defaultConfig, ...runtimeConfig };

  const log = (...args) => {
    if (config.debug) {
      console.log("[PWA]", ...args);
    }
  };

  const warn = (...args) => {
    if (config.debug) {
      console.warn("[PWA]", ...args);
    }
  };

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");

    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);

    for (let i = 0; i < rawData.length; i += 1) {
      outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
  }

  function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) {
      return parts.pop().split(";").shift();
    }
    return null;
  }

  async function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) {
      warn("Service workers are not supported in this browser.");
      return null;
    }

    try {
      const registration = await navigator.serviceWorker.register(config.serviceWorkerUrl, {
        scope: config.scope,
      });
      log("Service worker registered", registration.scope);
      window.dispatchEvent(
        new CustomEvent("pwa:service-worker-registered", {
          detail: { registration },
        })
      );
      return registration;
    } catch (error) {
      console.error("[PWA] Service worker registration failed", error);
      throw error;
    }
  }

  async function ensurePushSubscription(options = {}) {
    const merged = { vapidPublicKey: config.vapidPublicKey, ...options };
    if (!merged.vapidPublicKey) {
      throw new Error("Se requiere la clave pública VAPID para registrar notificaciones push.");
    }

    const registration = await navigator.serviceWorker.ready;
    let subscription = await registration.pushManager.getSubscription();

    if (!subscription) {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(merged.vapidPublicKey),
      });
      log("Push subscription creada");
    }

    if (config.subscriptionEndpoint) {
      try {
        await fetch(config.subscriptionEndpoint, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": getCookie("csrftoken") || "",
          },
          body: JSON.stringify({ subscription }),
        });
        log("Suscripción enviada al backend");
      } catch (error) {
        console.error("[PWA] No se pudo sincronizar la suscripción con el backend", error);
      }
    }

    return subscription;
  }

  function listenForSubscriptionChanges() {
    navigator.serviceWorker.addEventListener("message", (event) => {
      if (!event.data) {
        return;
      }
      if (event.data.type === "PUSH_SUBSCRIPTION_CHANGED") {
        warn("La suscripción push cambió, recreándola...");
        ensurePushSubscription().catch((error) => console.error("[PWA] Error recreando la suscripción", error));
      }
      if (event.data.type === "PUSH_NAVIGATION" && event.data.url) {
        window.location.href = event.data.url;
      }
    });
  }

  window.PWABridge = {
    registerServiceWorker,
    ensurePushSubscription,
  };

  if (config.autoRegister) {
    registerServiceWorker()
      .then(() => listenForSubscriptionChanges())
      .catch(() => {
        /* errores ya registrados */
      });
  }
})();
