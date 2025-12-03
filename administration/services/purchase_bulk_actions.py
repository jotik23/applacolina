from __future__ import annotations

from datetime import date, datetime
from typing import Any, Sequence

from django.db.models import Case, DecimalField, F, Q, When
from django.utils import timezone

from administration.models import PurchaseRequest


class PurchaseBulkActionError(Exception):
    """Raised when a bulk action payload is invalid."""


def move_purchases_to_status(*, purchase_ids: Sequence[int], target_status: str | None) -> int:
    """
    Moves the selected purchases to the requested status.

    Returns the number of purchases whose status actually changed.
    """

    if not target_status:
        raise PurchaseBulkActionError("Selecciona el estado destino para las compras seleccionadas.")
    valid_statuses = {value for value, _ in PurchaseRequest.Status.choices}
    if target_status not in valid_statuses:
        raise PurchaseBulkActionError("Selecciona un estado válido para continuar.")
    if not purchase_ids:
        return 0
    queryset = PurchaseRequest.objects.filter(pk__in=purchase_ids).exclude(status=target_status)
    if not queryset:
        return 0
    now = timezone.now()
    update_kwargs: dict[str, Any] = {'status': target_status, 'updated_at': now}
    if target_status in PurchaseRequest.POST_PAYMENT_STATUSES:
        zero_payment_condition = Q(payment_amount__isnull=True) | Q(payment_amount__lte=0)
        update_kwargs['payment_amount'] = Case(
            When(
                Q(invoice_total__isnull=False) & zero_payment_condition,
                then=F('invoice_total'),
            ),
            default=F('payment_amount'),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    return queryset.update(**update_kwargs)


def update_purchases_requested_date(*, purchase_ids: Sequence[int], requested_date: date | None) -> int:
    """
    Updates the ``created_at`` timestamp so that its local date matches the requested date.

    Returns the number of purchases that were updated.
    """

    if requested_date is None:
        raise PurchaseBulkActionError("Selecciona una fecha válida para las solicitudes.")
    if not purchase_ids:
        return 0
    tz = timezone.get_current_timezone()
    now = timezone.now()
    purchases = list(PurchaseRequest.objects.filter(pk__in=purchase_ids))
    if not purchases:
        return 0
    to_update: list[PurchaseRequest] = []
    for purchase in purchases:
        current = purchase.created_at
        if current:
            current_local = timezone.localtime(current, tz)
            base_time = current_local.time()
        else:
            current_local = timezone.localtime(now, tz)
            base_time = current_local.time()
        naive_target = datetime.combine(requested_date, base_time.replace(tzinfo=None))
        new_created_at = timezone.make_aware(naive_target, tz)
        if current and abs((current - new_created_at).total_seconds()) < 1:
            continue
        purchase.created_at = new_created_at
        purchase.updated_at = now
        to_update.append(purchase)
    if to_update:
        PurchaseRequest.objects.bulk_update(to_update, ['created_at', 'updated_at'])
    return len(to_update)
