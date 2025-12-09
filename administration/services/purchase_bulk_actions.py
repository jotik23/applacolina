from __future__ import annotations

from datetime import date, datetime
from typing import Any, Sequence

from django.db import transaction
from django.db.models import Case, DecimalField, F, Q, When
from django.utils import timezone

from administration.models import PurchaseRequest, PurchaseSupportAttachment


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
        invoice_positive = Q(invoice_total__gt=0)
        estimated_positive = Q(estimated_total__gt=0)
        update_kwargs['payment_amount'] = Case(
            When(
                zero_payment_condition & invoice_positive,
                then=F('invoice_total'),
            ),
            When(
                zero_payment_condition & ~invoice_positive & estimated_positive,
                then=F('estimated_total'),
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


def group_purchases_for_support(*, purchase_ids: Sequence[int]) -> tuple[str, int, str, int]:
    """
    Bundles the selected purchases under a single support group so they can share
    a unique support workflow.

    Returns a tuple with the generated group code, the leader ID, the leader timeline code and
    the total number of purchases grouped.
    """

    if not purchase_ids or len(purchase_ids) < 2:
        raise PurchaseBulkActionError("Selecciona al menos dos compras para crear un grupo de soporte.")
    with transaction.atomic():
        purchases = list(
            PurchaseRequest.objects.select_for_update()
            .filter(pk__in=purchase_ids)
            .order_by('created_at', 'pk')
        )
        if len(purchases) < 2:
            raise PurchaseBulkActionError("Selecciona al menos dos compras para crear un grupo de soporte.")
        for purchase in purchases:
            if purchase.status != PurchaseRequest.Status.INVOICE:
                raise PurchaseBulkActionError(
                    "Solo puedes agrupar compras que estén en el estado Gestionar soporte."
                )
            if purchase.support_group_code:
                raise PurchaseBulkActionError(
                    f"La compra {purchase.timeline_code} ya pertenece a un grupo de soporte."
                )
            if PurchaseSupportAttachment.objects.filter(purchase=purchase).exists():
                raise PurchaseBulkActionError(
                    f"La compra {purchase.timeline_code} ya tiene soportes adjuntos; elimínalos antes de agrupar."
                )
        leader = purchases[0]
        code = _generate_support_group_code()
        now = timezone.now()
        for purchase in purchases:
            purchase.support_group_code = code
            purchase.updated_at = now
            if purchase.pk == leader.pk:
                purchase.support_group_leader = None
            else:
                purchase.support_group_leader = leader
        PurchaseRequest.objects.bulk_update(
            purchases,
            ['support_group_code', 'support_group_leader', 'updated_at'],
        )
        return code, leader.pk, leader.timeline_code, len(purchases)


def _generate_support_group_code() -> str:
    today_prefix = timezone.localtime().strftime("SG-%Y%m%d")
    suffix = 1
    while True:
        candidate = f"{today_prefix}-{suffix:02d}"
        exists = PurchaseRequest.objects.filter(support_group_code=candidate).exists()
        if not exists:
            return candidate
        suffix += 1


def ungroup_support_group(*, purchase_id: int | None) -> tuple[str, int, PurchaseRequest]:
    """
    Reverts a support group so that every purchase becomes independent again.

    Returns a tuple with the former group code, the number of purchases affected and the leader instance.
    """

    if not purchase_id:
        raise PurchaseBulkActionError("Selecciona la compra que deseas desagrupar.")
    with transaction.atomic():
        try:
            purchase = PurchaseRequest.objects.select_for_update().get(pk=purchase_id)
        except PurchaseRequest.DoesNotExist as exc:
            raise PurchaseBulkActionError("La compra seleccionada ya no existe.") from exc
        if not purchase.support_group_code:
            raise PurchaseBulkActionError("Esta compra no pertenece a un grupo de soporte.")
        if purchase.support_group_leader_id:
            raise PurchaseBulkActionError("Solo el líder del grupo puede revertir la agrupación.")
        group_code = purchase.support_group_code
        members = list(
            PurchaseRequest.objects.select_for_update()
            .filter(support_group_code=group_code)
            .order_by('pk')
        )
        now = timezone.now()
        for member in members:
            member.support_group_code = ''
            member.support_group_leader = None
            member.updated_at = now
        PurchaseRequest.objects.bulk_update(members, ['support_group_code', 'support_group_leader', 'updated_at'])
        purchase.refresh_from_db(fields=['support_group_code', 'support_group_leader', 'status'])
        return group_code, len(members), purchase
