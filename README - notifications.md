## Módulo de notificaciones

Este documento describe la arquitectura del nuevo módulo `notifications`, creado para centralizar la orquestación de avisos transversales a la plataforma y habilitar el envío de mensajes vía Telegram.

### Objetivos
- Modelar las entidades necesarias para administrar bots, chats vinculados y preferencias por usuario.
- Permitir la definición de tópicos y plantillas reutilizables entre calendarios, tareas y futuros componentes.
- Persistir eventos y despachos para garantir trazabilidad y auditoría.
- Suministrar un cliente de Telegram listo para usar desde servicios y tareas en background.

---

## Modelos principales

### `TelegramBotConfig`
Representa cada bot configurado:
- Token, estado, parse mode por defecto y webhook opcional.
- Lista de `allowed_updates` configurables y `test_chat_id` para validaciones rápidas.
- Utilice la administración de Django para activar/desactivar bots sin tocar variables de entorno.

### `TelegramChatLink`
Enlaza un `UserProfile` con un chat específico:
- Guarda `chat_id`, `telegram_user_id`, metadatos de la persona y el estado del enlace.
- Almacena el `link_token` (UUID) usado para validar que el colaborador realmente hizo `/start`.
- Usa `status` (`pending`, `verified`, `blocked`, `archived`) y fechas de verificación/última interacción.

### `NotificationTopic`
Catálogo de eventos (p. ej. `calendar_shift_assigned`, `task_due`):
- Define el canal por defecto y si el tópico está activo.
- Las apps cliente solo deben referenciar el `slug` para generar eventos.

### `NotificationPreference`
Registra suscripciones por usuario:
- Canal (`telegram`, `email`, `sms`), bot preferido (opcional) y filtros contextuales (`farm`, `chicken_house`, `room`, `position`).
- `quiet_hours_start/quiet_hours_end` permiten silenciar mensajes en ventanas específicas.
- `UniqueConstraint` evita preferencias duplicadas con el mismo contexto.

### `NotificationTemplate`
Plantillas versionadas por tópico/canal/idioma:
- `body_template` usa motor estándar de Django (`Context` + `Template`).
- `keyboard` y `extra_options` almacenan la estructura de botones o parámetros adicionales (p. ej. `disable_notification`).
- `parse_mode` puede sobreescribir el valor por defecto del bot.

### `NotificationEvent`
Registro abstracto del evento:
- Guarda el tópico, `source_app`, `source_identifier` (ej. ID del calendario) y el `context` normalizado.
- No despacha mensajes por sí mismo; sirve como ancla para crear múltiples `NotificationDispatch`.

### `NotificationDispatch`
Representa cada intento de envío:
- Campos `status` (`pending`, `processing`, `sent`, `failed`, `skipped`, `cancelled`), `scheduled_for`, `sent_at`, `attempt_count`.
- `payload` almacena el mensaje final renderizado (texto + op extras).
- `response_meta` persiste la respuesta completa del canal para auditoría.

### `TelegramInboundUpdate`
Persiste updates entrantes (webhook o polling):
- Permite procesar comandos como `/start`, callbacks de botones o diagnosticar errores.

---

## Flujo recomendado

1. **Configurar bot**: crear `TelegramBotConfig` (admin de Django) con nombre y token. Opcionalmente definir `allowed_updates`, parse mode y webhook actual.
2. **Vincular colaboradores**:
   - Generar `TelegramChatLink` (status `pending`) al mostrar un QR o URL con `link_token`.
   - Al recibir `/start`, validar token y llamar `chat_link.mark_verified()`.
3. **Definir tópicos, preferencias y plantillas** desde el admin.
4. **Crear eventos** desde cualquier app: `NotificationEvent.objects.create(topic=..., context=...)`.
5. **Construir despachos** (`NotificationDispatch`) usando la plantilla apropiada y asignando el `chat_link` correspondiente.
6. **Enviar al canal** usando `TelegramNotificationSender`.

*Nota:* El módulo no automatiza aún la generación de despachos desde eventos; se integra en futuras iteraciones.

---

## Servicio de Telegram

El archivo `notifications/services/telegram.py` expone:

```python
from notifications.services import TelegramNotificationSender

dispatch = NotificationDispatch.objects.get(pk=...)
sender = TelegramNotificationSender(timeout=10.0)
response = sender.send_dispatch(dispatch)
```

- El `payload` del despacho debe contener, como mínimo, `text`.
- Se admiten campos opcionales: `parse_mode`, `disable_notification`, `reply_markup`, `link_preview_options`, `entities`, `message_thread_id` y `extra` (dict con llaves arbitrarias aceptadas por la API).
- El servicio maneja cambios de estado (`mark_processing`, `mark_sent`, `mark_failed`) y actualiza `last_interaction_at` del chat.
- Ante error de red (`httpx.HTTPError`) o respuesta negativa de Telegram, se lanza `TelegramNotificationError` y el despacho queda en `failed`.

---

## Administración (Back Office)

- Los modelos están registrados en el panel de Django con búsquedas y filtros clave.
- `TelegramBotConfig`: muestra URL base, estado y fecha de última sincronización.
- `TelegramChatLink`: permite revisar/verificar enlaces y detectar usuarios bloqueados.
- `NotificationPreference`: usa autocompletado para cruzar usuarios, tópicos y jerarquías (granja/galpón/salón/posición).
- `NotificationDispatch`: expone intentos, respuestas y errores para depuración.

> Sugerencia: limite el acceso a estos módulos a personal con permisos de operaciones o TI debido a la sensibilidad del token.

---

## Requisitos e instalación

Se añadió la dependencia `httpx` al archivo `requirements.txt`. Al reconstruir la imagen Docker o actualizar el entorno virtual:

```bash
pip install -r requirements.txt
```

Ejecute migraciones:

```bash
docker compose exec web python manage.py migrate notifications
```

---

## Próximos pasos sugeridos

1. Implementar un servicio que renderice plantillas (`NotificationTemplate`) con el contexto de `NotificationEvent`.
2. Conectar los generadores de calendarios y tareas para crear eventos/ despachos automáticamente.
3. Implementar vistas o endpoints para gestionar el flujo `/start` ↔ `TelegramChatLink`.
4. Añadir tareas en background (Celery/cron) para consumir `NotificationDispatch` en estado `pending`.

--- 

## Referencias rápidas

- Modelo principal: `notifications.models`
- Cliente Telegram: `notifications.services.telegram`
- Documentación oficial Telegram Bot API: <https://core.telegram.org/bots/api>

Este módulo sienta las bases para centralizar la mensajería multi-canal y crecer gradualmente hacia SMS, email u otras integraciones reutilizando los mismos tópicos, preferencias y despachos.

