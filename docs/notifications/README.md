# Notificaciones configuradas

Este archivo sirve como bitácora rápida de los flujos de notificaciones push implementados en la mini app.

## Servicio genérico

- Módulo: `task_manager/services/push_notifications.py`.
- Expone `PushNotificationService` y los DTO `PushNotificationMessage`/`PushNotificationAction`.
- Las suscripciones viven en `MiniAppPushSubscription`; el servicio ignora usuarios sin llaves o sin suscripciones activas y desactiva las que reporten 404/410.
- Las integraciones de dominio deben usar los helpers de `task_manager/services/purchase_notifications.py` para obtener textos y CTAs coherentes.

## Tipos registrados

| Código | Descripción | Trigger | Destinatario | CTA |
| --- | --- | --- | --- | --- |
| `purchase.workflow-result` | Resultado final del workflow de aprobación (aprobado o devuelto a borrador). | `mini_app_purchase_approval_view` cuando `PurchaseApprovalDecisionService` completa el flujo o lo regresa a borrador. | Solicitante (`PurchaseRequest.requester`). | `Ver detalle` → `mini-app` con `purchaseId` preseleccionado. |
| `purchase.manager-assigned` | Aviso para el gestor asignado a una compra. | Cambio de `assigned_manager_id` al aprobar desde la mini app o al guardar/confirmar la orden en administración. | Gestor asignado (`PurchaseRequest.assigned_manager`). | `Gestionar compra` → vista de compras en la mini app. |
| `purchase.returned-for-adjustments` | Solicitud devuelta a borrador con comentarios. | `mini_app_purchase_request_modify_view` cuando un gestor pide ajustes. | Solicitante (`PurchaseRequest.requester`). | `Editar y reenviar` → vista de compras en la mini app. |

> Nota: si se agregan nuevos flujos, documentarlos aquí para mantener visibilidad entre equipos.
