from __future__ import annotations

from django.db.models import F, Q, QuerySet
from django.utils import timezone

from administration.models import PurchaseRequest


def get_purchases_missing_payment_amount_queryset() -> QuerySet[PurchaseRequest]:
    """
    Returns the queryset of purchases whose invoice total is set but the paid amount remains empty.
    """

    zero_payment_condition = Q(payment_amount__isnull=True) | Q(payment_amount__lte=0)
    return PurchaseRequest.objects.filter(
        status__in=PurchaseRequest.POST_PAYMENT_STATUSES,
        invoice_total__gt=0,
    ).filter(zero_payment_condition)


def reset_missing_payment_amounts() -> int:
    """
    Copies the invoice total into the payment amount for purchases that were moved past the payment stage.
    """

    queryset = get_purchases_missing_payment_amount_queryset()
    now = timezone.now()
    return queryset.update(payment_amount=F('invoice_total'), updated_at=now)
