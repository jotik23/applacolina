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
   /Users/jhonvergara/Documents/APP Secrets /PWA La Colina /android.keystore
   BLOST..1
   ```
6. Compila:
   ```bash
   bubblewrap build
   ```
   Esto genera `app-release-signed.apk` (para sideload) y `app-release-bundle.aab` (si luego quieres distribuir via Play/MDM).
7. Instala internamente con ADB, MDM o compartiendo el APK firmado.

> Si necesitas regenerar el paquete, solo vuelve a ejecutar `bubblewrap build`. Mantén el mismo keystore para que las actualizaciones se instalen sin desinstalar.

## 3. Notificaciones push con Firebase Cloud Messaging (Railway)

El service worker ya maneja `push`, `notificationclick` y `pushsubscriptionchange`. Para conectarlo a Firebase y Railway sigue estos pasos:

### 3.1 Configuración en Firebase
1. Crea (o reutiliza) un proyecto en [Firebase Console](https://console.firebase.google.com/).
2. Habilita **Cloud Messaging** y registra una aplicación Web (ej: `lacolina-miniapp`). Agrega el dominio `https://applacolina-production.up.railway.app` como origin autorizado.
3. En *Project Settings › Cloud Messaging › Web configuration* genera un **key pair**. Copia la **Public key (VAPID)** y guarda también la Private key/Server key; se usan para enviar mensajes.
4. Descarga las credenciales de servicio (`firebase-admin` JSON) si vas a disparar mensajes desde Django o desde un worker en Railway.

### 3.2 Variables en Railway
1. En el panel del servicio (Project › Variables) agrega:
   - `WEB_PUSH_PUBLIC_KEY`: pega la public key VAPID generada en Firebase.
   - `WEB_PUSH_PRIVATE_KEY`: la clave privada asociada (para firmar envíos desde Django).
   - `WEB_PUSH_CONTACT`: correo o URL de contacto (formato `mailto:`) que Firebase mostrará como owner del canal.
   - `WEB_PUSH_SUBSCRIPTION_ENDPOINT`: apunta a `https://applacolina-production.up.railway.app/task-manager/api/pwa/subscriptions/` (vista incluida en este repo).
2. Redepliega para que Django propague esos valores. `task_manager/views.py` inyecta esta info en `window.PWAPushConfig`, y `pwa-init.js` la usa para registrar la suscripción.

> Nota: puedes copiar la clave privada exactamente como la provee Firebase (PEM). El backend la normaliza internamente para generar el formato base64 requerido por Web Push, así que no necesitas convertirla manualmente.

3. **Vincula el dominio con el APK (Trusted Web Activity)**
   - Define `ANDROID_TWA_PACKAGE_NAME` (ej. `com.lacolina.taskmanager`) y `ANDROID_TWA_SHA256_FINGERPRINTS` (lista separada por comas con los fingerprints SHA‑256 de tu keystore) en Railway.
   - Django expone automáticamente `https://applacolina-production.up.railway.app/.well-known/assetlinks.json` con esos valores, que es lo que Chrome verifica para abrir la TWA sin barra de URL.
   - Cada vez que generes un keystore nuevo debes actualizar la variable de fingerprint.

### 3.3 Guardar suscripciones en tu backend
1. La ruta `POST /task-manager/api/pwa/subscriptions/` ya valida sesión + permiso `access_mini_app` y persiste el JSON (`endpoint`, `keys.p256dh`, `keys.auth`, expiración, cliente, user-agent) en `MiniAppPushSubscription`.
2. Con las variables anteriores, `pwa-init.js` envía automáticamente la suscripción a ese endpoint cada vez que el usuario concede permisos.
3. La interfaz muestra un botón “Activar notificaciones” (atributo `[data-enable-push]`) en el header de la mini app que ejecuta `window.PWABridge.ensurePushSubscription()` y refleja los estados al usuario.
4. Para pruebas internas existe la vista `GET/POST /task-manager/tools/push-test/` (solo staff) donde puedes seleccionar el usuario, elegir la suscripción almacenada y disparar un push ad hoc sin exponerlo en el menú.

### 3.4 Enviar notificaciones desde Firebase
1. Usa la **Server key** o un service account para llamar a `https://fcm.googleapis.com/fcm/send` pasando el `endpoint` (o usando `topic`s si los agrupas).
2. En Python puedes instalar `firebase-admin` dentro de Railway y disparar mensajes desde un management command o Celery worker, enviando la suscripción guardada.
3. También puedes crear una Cloud Function en Firebase que lea las suscripciones desde Firestore/postgres y envié notificaciones (ideal para integrarlo con eventos del sistema).

### 3.5 Pruebas
1. Abre la mini app en Chrome/Android, ve a DevTools › Application › Service Workers y confirma que `service-worker.js` está *activated*.
2. Concede permisos de notificación y ejecuta el snippet de `data-enable-push` para registrar al usuario.
3. Desde Firebase Console › Cloud Messaging envía un mensaje de prueba dirigido al token guardado; el service worker lo mostrará con los íconos definidos.
4. También puedes usar DevTools › Application › Service Workers › “Push” para simular payloads JSON y verificar la UI offline.

## 4. Checklist previo a distribución

- [ ] Manifest sin advertencias en PWABuilder.
- [ ] Service worker activo (`chrome://inspect/#service-workers`).
- [ ] Permiso push concedido y suscripción almacenada.
- [ ] APK firmado y probado en al menos un dispositivo Android 12+.
- [ ] Proceso documentado para reinstalar/actualizar (mantener keystore y versionCode).

Con todo esto puedes subir la URL a PWABuilder, generar el APK en minutos y desplegarlo internamente con push habilitado.
