# PWA Builder · Granjas La Colina

Guía rápida para empaquetar la mini app en Android usando [PWA Builder](https://www.pwabuilder.com/) + Bubblewrap y habilitar notificaciones push.

## 1. Prerrequisitos ya cubiertos

- Manifesto en `static/task_manager/pwa/manifest.json` (name, icons, theme, scope, shortcuts).
- Service worker en `/service-worker.js` con caché básico, fallback offline y listeners de push.
- Registro automático y helper JS en `static/task_manager/pwa/pwa-init.js`.
- Íconos 48–512 px y pantalla offline en `static/task_manager/pwa/`.

Solo asegúrate de que la URL `https://applacolina-production.up.railway.app/task-manager/telegram/mini-app/` esté accesible (HTTPS obligatorio).

## 2. Generar el paquete Android (Trusted Web Activity)

1. Valida la PWA en [pwabuilder.com](https://www.pwabuilder.com/report?site=https://applacolina-production.up.railway.app/task-manager/telegram/mini-app/).
2. Descarga el paquete Android y descomprímelo dentro de un directorio temporal.
3. Instala Bubblewrap una única vez:
   ```bash
   npm i -g @bubblewrap/cli
   ```
4. Inicializa el proyecto TWA (usar el bundle `com.lacolina.taskmanager` o similar):
   ```bash
   bubblewrap init --manifest https://applacolina-production.up.railway.app/static/task_manager/pwa/manifest.json
   ```
5. Configura la firma (usar/crear tu `keystore.jks`). Ejemplo:
   ```bash
   keytool -genkey -v -keystore lacolina.keystore -alias lacolina -keyalg RSA -keysize 2048 -validity 10000
   bubblewrap signing --ks lacolina.keystore --alias lacolina
   ```
6. Compila:
   ```bash
   bubblewrap build
   ```
   Esto genera `app-release-signed.apk` (para sideload) y `app-release-bundle.aab` (si luego quieres distribuir via Play/MDM).
7. Instala internamente con ADB, MDM o compartiendo el APK firmado.

> Si necesitas regenerar el paquete, solo vuelve a ejecutar `bubblewrap build`. Mantén el mismo keystore para que las actualizaciones se instalen sin desinstalar.

## 3. Notificaciones push (web → Android)

El service worker expone los eventos `push`, `notificationclick` y `pushsubscriptionchange`. Para activarlos necesitas:

1. **Elegir emisor**
   - **FCM Web Push** (control total): crea un proyecto Firebase, habilita Cloud Messaging y genera claves VAPID.
   - **OneSignal**: crea una app, habilita "Web Push" y usa sus claves públicas. Puedes seguir usando OneSignal para disparar campañas sin backend propio.

2. **Configurar la clave pública (VAPID / OneSignal)**
   - Exporta la clave a la variable `WEB_PUSH_PUBLIC_KEY` (env) o define `window.PWAPushConfig.vapidPublicKey` manualmente en el template.
   - Opcional: define `WEB_PUSH_SUBSCRIPTION_ENDPOINT` o asigna `window.PWAPushConfig.subscriptionEndpoint` a un endpoint tuyo que guarde las suscripciones (por ejemplo, una view Django que almacene el JSON en la base).

3. **Guardar la suscripción**
   - `pwa-init.js` expone `window.PWABridge.ensurePushSubscription(...)`. Llama a esta función cuando el usuario conceda permisos y envía el JSON al backend o a OneSignal según corresponda.
   - Ejemplo mínimo:
     ```javascript
     document.querySelector('[data-enable-push]').addEventListener('click', async () => {
       try {
         const subscription = await window.PWABridge.ensurePushSubscription();
         console.log('Suscripción activa', subscription);
       } catch (error) {
         console.error('No se pudo activar push', error);
       }
     });
     ```

4. **Enviar mensajes**
   - Con FCM: usa la API `fcm.googleapis.com/fcm/send` o Cloud Functions apuntando a los endpoints guardados.
   - Con OneSignal: usa su panel o la API REST y selecciona los usuarios/web push device IDs.

5. **Probar**
   - Navega a la mini app en Chrome, abre DevTools › Application › Service Workers para verificar el registro.
   - Usa "Push" → "Payload" en DevTools o la API de tu proveedor para enviar un mensaje de prueba. El service worker mostrará la notificación con icono/badge definidos.

## 4. Checklist previo a distribución

- [ ] Manifest sin advertencias en PWABuilder.
- [ ] Service worker activo (`chrome://inspect/#service-workers`).
- [ ] Permiso push concedido y suscripción almacenada.
- [ ] APK firmado y probado en al menos un dispositivo Android 12+.
- [ ] Proceso documentado para reinstalar/actualizar (mantener keystore y versionCode).

Con todo esto puedes subir la URL a PWABuilder, generar el APK en minutos y desplegarlo internamente con push habilitado.
