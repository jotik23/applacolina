from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

from django.urls import reverse
from django.utils.translation import gettext as _

from administration.models import PurchaseRequest
from personal.models import UserProfile

from task_manager.services.push_notifications import (
    PushNotificationAction,
    PushNotificationMessage,
    PushNotificationResult,
    PushNotificationService,
)

_SERVICE_SINGLETON: PushNotificationService | None = None


def _get_service(service: PushNotificationService | None = None) -> PushNotificationService:
    global _SERVICE_SINGLETON
    if service:
        return service
    if _SERVICE_SINGLETON is None:
        _SERVICE_SINGLETON = PushNotificationService()
    return _SERVICE_SINGLETON


def notify_purchase_workflow_result(
    *,
    purchase: PurchaseRequest,
    decision: str,
    workflow_completed: bool,
    approver: UserProfile,
    service: PushNotificationService | None = None,
) -> PushNotificationResult | None:
    requester = purchase.requester
    if not requester:
        return None
    if decision != "rejected" and not workflow_completed:
        return None

    status_label = _("rechazada") if decision == "rejected" else _("aprobada")
    next_step = (
        _("La edición de la solicitud")
        if decision == "rejected"
        else _("La gestión de la compra")
    )
    approver_label = _display_name(approver)
    title = _("La compra solicitada fue %(status)s") % {"status": status_label}
    body = _(
        "La compra de %(name)s fue %(status)s por %(approver)s. %(next_step)s ya está disponible."
    ) % {
        "name": purchase.name,
        "status": status_label,
        "approver": approver_label,
        "next_step": next_step,
    }

    message = PushNotificationMessage(
        title=title,
        body=body,
        data={
            "purchase_id": purchase.pk,
            "purchase_status": purchase.status,
            "notification_type": "purchase.workflow-result",
            "decision": decision,
        },
        action=PushNotificationAction(
            label=_("Ver detalle"),
            url=_build_purchase_cta_url(purchase.pk, utm_source="push-workflow"),
        ),
        require_interaction=True,
    )
    return _get_service(service).send_to_user(
        user=requester,
        message=message,
        notification_type="purchase.workflow-result",
    )


def notify_purchase_manager_assignment(
    *,
    purchase: PurchaseRequest,
    manager: UserProfile | None,
    service: PushNotificationService | None = None,
    source: str = "workflow",
) -> PushNotificationResult | None:
    if not manager:
        return None
    message = PushNotificationMessage(
        title=_("¡Una nueva compra!"),
        body=_("La compra de %(name)s te ha sido asignada; el equipo espera tu pronta gestión.")
        % {"name": purchase.name},
        data={
            "purchase_id": purchase.pk,
            "purchase_status": purchase.status,
            "notification_type": "purchase.manager-assigned",
            "source": source,
        },
        action=PushNotificationAction(
            label=_("Gestionar compra"),
            url=_build_purchase_cta_url(purchase.pk, utm_source="push-manager"),
        ),
        require_interaction=False,
    )
    return _get_service(service).send_to_user(
        user=manager,
        message=message,
        notification_type="purchase.manager-assigned",
    )


def notify_purchase_returned_for_changes(
    *,
    purchase: PurchaseRequest,
    manager: UserProfile | None,
    reason: str | None,
    service: PushNotificationService | None = None,
) -> PushNotificationResult | None:
    requester = purchase.requester
    if not requester:
        return None
    reason_text = (reason or "").strip()
    if not reason_text:
        reason_text = _("Sin comentarios adicionales.")
    reason_excerpt = reason_text[:240]
    manager_label = _display_name(manager)
    message = PushNotificationMessage(
        title=_("La compra de %(name)s volvió a edición") % {"name": purchase.name},
        body=_('%(manager)s pidió estos cambios: "%(reason)s". Ajusta y reenvía para revisión.')
        % {
            "manager": manager_label,
            "reason": reason_excerpt,
        },
        data={
            "purchase_id": purchase.pk,
            "purchase_status": purchase.status,
            "notification_type": "purchase.returned-for-adjustments",
        },
        action=PushNotificationAction(
            label=_("Editar y reenviar"),
            url=_build_purchase_cta_url(purchase.pk, utm_source="push-adjustments", view="purchases"),
        ),
        require_interaction=True,
    )
    return _get_service(service).send_to_user(
        user=requester,
        message=message,
        notification_type="purchase.returned-for-adjustments",
    )


def _build_purchase_cta_url(
    purchase_id: int,
    *,
    utm_source: str = "push",
    view: str = "purchases",
) -> str:
    base_url = reverse("task_manager:telegram-mini-app")
    query = urlencode(
        {
            "view": view,
            "purchaseId": purchase_id,
            "utm_source": utm_source,
        }
    )
    return f"{base_url}?{query}"


def _display_name(user: Optional[UserProfile]) -> str:
    if not user:
        return _("Equipo de compras")
    get_full_name = getattr(user, "get_full_name", None)
    if callable(get_full_name):
        label = (get_full_name() or "").strip()
        if label:
            return label
    username = getattr(user, "get_username", None)
    if callable(username):
        label = (username() or "").strip()
        if label:
            return label
    return str(user)
