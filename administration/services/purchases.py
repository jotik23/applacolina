from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Literal, Sequence

from django.db import models
from django.utils import timezone

from administration.models import PurchaseApproval, PurchaseRequest

StageStatus = Literal['pending', 'active', 'completed', 'locked']


@dataclass(frozen=True)
class StageIndicator:
    code: str
    label: str
    status: StageStatus
    tooltip: str


@dataclass(frozen=True)
class PurchaseAction:
    label: str
    panel: str
    verb: str


@dataclass(frozen=True)
class PurchaseRecord:
    pk: int
    timeline_code: str
    requester: str
    supplier: str
    scope_label: str
    area_label: str
    category_name: str
    support_type_label: str | None
    approvals_received: Sequence[str]
    approvals_pending: Sequence[str]
    lifecycle: str
    created_on: timezone.datetime
    eta: timezone.datetime | None
    currency: str
    total_amount: Decimal
    stage_indicators: Sequence[StageIndicator]
    current_stage: StageIndicator | None
    action: PurchaseAction | None
    status_badge: str
    status_palette: str
    description: str
    has_reception_mismatch: bool
    paid_amount: Decimal
    show_payment_breakdown: bool

    @property
    def total_display(self) -> str:
        return f'{self.currency} {self.total_amount:,.2f}'

    @property
    def paid_display(self) -> str:
        return f'{self.currency} {self.paid_amount:,.2f}'


@dataclass(frozen=True)
class PurchaseScope:
    code: str
    label: str
    description: str
    count: int


@dataclass(frozen=True)
class PurchasePanel:
    code: str
    title: str
    template_name: str


@dataclass(frozen=True)
class PurchasePanelState:
    panel: PurchasePanel
    purchase: PurchaseRequest | None


@dataclass(frozen=True)
class PurchaseDashboardState:
    scope: PurchaseScope
    scopes: Sequence[PurchaseScope]
    purchases: Sequence[PurchaseRecord]
    panel: PurchasePanelState | None
    recent_activity: Sequence[dict[str, str]]


PANEL_REGISTRY = {
    'request': PurchasePanel('request', 'Nueva solicitud de compra', 'administration/purchases/forms/_form_request.html'),
    'order': PurchasePanel('order', 'Gestionar compra', 'administration/purchases/forms/_form_order.html'),
    'reception': PurchasePanel('reception', 'Registrar recepción', 'administration/purchases/forms/_form_reception.html'),
    'invoice': PurchasePanel('invoice', 'Gestionar soporte', 'administration/purchases/forms/_form_invoice.html'),
    'payment': PurchasePanel('payment', 'Registrar pago', 'administration/purchases/forms/_form_payment.html'),
    'accounting': PurchasePanel(
        'accounting',
        'Registrar en sistema contable',
        'administration/purchases/forms/_form_accounting.html',
    ),
}


PURCHASE_STAGE_META = {
    'draft': {
        'label': 'Solicitud (en borrador)',
        'description': 'Solicitudes en preparación antes de enviarse.',
        'tooltip': 'Solicitud creada y aún editable.',
        'palette': 'slate',
    },
    'approval': {
        'label': 'En aprobación',
        'description': 'Esperando visto bueno del flujo configurado.',
        'tooltip': 'En evaluación por los aprobadores.',
        'palette': 'amber',
    },
    'purchasing': {
        'label': 'En Gestión de compra',
        'description': 'Equipo de compras gestionando la orden.',
        'tooltip': 'Aprobada y en proceso de compra.',
        'palette': 'indigo',
    },
    'payable': {
        'label': 'Revisar pago',
        'description': 'Verifica los datos bancarios y programa la transferencia.',
        'tooltip': 'Aún falta registrar o confirmar el pago.',
        'palette': 'orange',
    },
    'receiving': {
        'label': 'Esperando llegada',
        'description': 'Órdenes emitidas pendientes de recepción.',
        'tooltip': 'Esperando recepción parcial o total.',
        'palette': 'blue',
    },
    'support': {
        'label': 'Gestionar soporte',
        'description': 'Valida y adjunta los soportes contables antes de enviarlos a contabilidad.',
        'tooltip': 'Revisa y completa el soporte documental.',
        'palette': 'emerald',
    },
    'accounting': {
        'label': 'En contabilidad',
        'description': 'Pagos enviados al equipo contable.',
        'tooltip': 'Contabilidad revisando y cerrando la compra.',
        'palette': 'cyan',
    },
    'archived': {
        'label': 'Archivadas',
        'description': 'Compras cerradas y con soporte completo.',
        'tooltip': 'Proceso completado y archivado.',
        'palette': 'slate',
    },
}


_STAGE_STATUS_MAP = dict(PurchaseRequest.STAGE_FLOW)
BASE_SCOPE_STAGE_ORDER = (
    "draft",
    "approval",
    "purchasing",
    "payable",
    "support",
    "accounting",
    "archived",
)
SCOPE_DEFINITIONS = tuple(
    (
        _STAGE_STATUS_MAP[stage_code],
        PURCHASE_STAGE_META[stage_code]['label'],
        PURCHASE_STAGE_META[stage_code]['description'],
    )
    for stage_code in BASE_SCOPE_STAGE_ORDER
    if stage_code in _STAGE_STATUS_MAP
)

WAITING_SCOPE_CODE = "waiting_arrival"
WAITING_SCOPE_LABEL = PURCHASE_STAGE_META['receiving']['label']
WAITING_SCOPE_DESCRIPTION = PURCHASE_STAGE_META['receiving']['description']
WAITING_SCOPE_STATUSES = {
    PurchaseRequest.Status.RECEPTION,
    PurchaseRequest.Status.INVOICE,
    PurchaseRequest.Status.PAYMENT,
}

STATUS_BADGES = {
    status: (PURCHASE_STAGE_META[stage_code]['label'], PURCHASE_STAGE_META[stage_code]['palette'])
    for stage_code, status in PurchaseRequest.STAGE_FLOW
}

ACTION_BY_STATUS = {
    PurchaseRequest.Status.DRAFT: PurchaseAction('Solicitar aprobación', 'request', 'solicitar_aprobacion'),
    PurchaseRequest.Status.SUBMITTED: PurchaseAction('Ver solicitud', 'request', 'ver_detalle'),
    PurchaseRequest.Status.APPROVED: PurchaseAction('Gestionar compra', 'order', 'gestionar_compra'),
    PurchaseRequest.Status.ORDERED: PurchaseAction('Registrar recepción', 'reception', 'registrar_recepcion'),
    PurchaseRequest.Status.RECEPTION: PurchaseAction('Registrar pago', 'payment', 'registrar_pago'),
    PurchaseRequest.Status.INVOICE: PurchaseAction('Gestionar soporte', 'invoice', 'registrar_factura'),
    PurchaseRequest.Status.PAYMENT: PurchaseAction(
        'Registrar en sistema contable',
        'accounting',
        'registrar_contabilidad',
    ),
    PurchaseRequest.Status.ARCHIVED: None,
}

def get_dashboard_state(*, scope_code: str | None, panel_code: str | None, purchase_pk: int | None) -> PurchaseDashboardState:
    scopes = _build_scopes()
    selected_scope = _find_scope(scopes, scope_code or scopes[0].code)
    purchases = tuple(_build_purchase_record(p, scope_code=selected_scope.code) for p in _query_purchases(selected_scope.code))
    panel_state = _resolve_panel(panel_code, purchase_pk)
    activity = _recent_activity()
    return PurchaseDashboardState(
        scope=selected_scope,
        scopes=scopes,
        purchases=purchases,
        panel=panel_state,
        recent_activity=activity,
    )


def _build_scopes() -> Sequence[PurchaseScope]:
    counts = {code: 0 for code, *_ in SCOPE_DEFINITIONS}
    qs = (
        PurchaseRequest.objects.values('status')
        .order_by('status')
        .annotate(count=models.Count('id'))  # type: ignore[name-defined]
    )
    for row in qs:
        counts[row['status']] = row['count']
    scopes: list[PurchaseScope] = [
        PurchaseScope(code=code, label=label, description=description, count=counts.get(code, 0))
        for code, label, description in SCOPE_DEFINITIONS
    ]
    waiting_count = (
        PurchaseRequest.objects.filter(
            delivery_condition=PurchaseRequest.DeliveryCondition.SHIPPING,
            status__in=WAITING_SCOPE_STATUSES,
        ).count()
    )
    scopes.insert(
        BASE_SCOPE_STAGE_ORDER.index('payable') + 1,
        PurchaseScope(
            code=WAITING_SCOPE_CODE,
            label=WAITING_SCOPE_LABEL,
            description=WAITING_SCOPE_DESCRIPTION,
            count=waiting_count,
        ),
    )
    return tuple(scopes)


def _find_scope(scopes: Sequence[PurchaseScope], code: str) -> PurchaseScope:
    for scope in scopes:
        if scope.code == code:
            return scope
    return scopes[0]


def _query_purchases(scope_code: str) -> Iterable[PurchaseRequest]:
    if scope_code == WAITING_SCOPE_CODE:
        return (
            PurchaseRequest.objects.select_related(
                'supplier',
                'requester',
                'expense_type',
                'support_document_type',
                'scope_farm',
                'scope_chicken_house__farm',
            )
            .prefetch_related('approvals__approver')
            .filter(
                delivery_condition=PurchaseRequest.DeliveryCondition.SHIPPING,
                status__in=WAITING_SCOPE_STATUSES,
            )
            .order_by('-created_at')[:50]
        )
    return (
        PurchaseRequest.objects.select_related(
            'supplier',
            'requester',
            'expense_type',
            'support_document_type',
            'scope_farm',
            'scope_chicken_house__farm',
        )
        .prefetch_related('approvals__approver')
        .filter(status=scope_code)
        .order_by('-created_at')[:50]
    )


def _build_purchase_record(purchase: PurchaseRequest, *, scope_code: str | None = None) -> PurchaseRecord:
    requester_name = ""
    if purchase.requester:
        requester_name = purchase.requester.get_full_name() or purchase.requester.get_username()
    else:
        requester_name = "Sistema"
    badge, palette = STATUS_BADGES.get(purchase.status, ('Sin estado', 'slate'))
    stage_indicators = tuple(
        StageIndicator(
            code=stage_code,
            label=PURCHASE_STAGE_META[stage_code]['label'],
            status=purchase.stage_status(stage_code),
            tooltip=PURCHASE_STAGE_META[stage_code]['tooltip'],
        )
        for stage_code, _ in PurchaseRequest.STAGE_FLOW
    )
    current_stage = next((stage for stage in stage_indicators if stage.status == 'active'), None)
    action = ACTION_BY_STATUS.get(purchase.status)
    is_waiting_scope = scope_code == WAITING_SCOPE_CODE
    if (
        is_waiting_scope
        and purchase.delivery_condition == PurchaseRequest.DeliveryCondition.SHIPPING
    ):
        action = PurchaseAction('Registrar entrega', 'reception', 'registrar_entrega')
    approvals = tuple(purchase.approvals.all())
    approvals_received = tuple(
        _format_approval_actor(approval) for approval in approvals if approval.status == PurchaseApproval.Status.APPROVED
    )
    approvals_pending = tuple(
        _format_approval_actor(approval) for approval in approvals if approval.status == PurchaseApproval.Status.PENDING
    )
    paid_amount = purchase.payment_amount or Decimal('0')
    show_payment_breakdown = purchase.show_payment_breakdown
    return PurchaseRecord(
        pk=purchase.pk,
        timeline_code=purchase.timeline_code,
        requester=requester_name,
        supplier=purchase.supplier.name,
        scope_label=purchase.scope_label,
        area_label=purchase.area_label,
        category_name=purchase.expense_type.name,
        support_type_label=purchase.support_document_type.name if purchase.support_document_type else None,
        approvals_received=approvals_received,
        approvals_pending=approvals_pending,
        lifecycle=purchase.status,
        created_on=purchase.created_at,
        eta=purchase.eta,
        currency=purchase.currency,
        total_amount=purchase.estimated_total,
        stage_indicators=stage_indicators,
        current_stage=current_stage,
        action=action,
        status_badge=badge,
        status_palette=palette,
        description=purchase.description or purchase.name,
        has_reception_mismatch=purchase.reception_mismatch,
        paid_amount=paid_amount,
        show_payment_breakdown=show_payment_breakdown,
    )


def _format_approval_actor(approval: PurchaseApproval) -> str:
    if approval.approver:
        return approval.approver.get_full_name() or approval.approver.get_username()
    return approval.role or "Pendiente asignación"


def _resolve_panel(panel_code: str | None, purchase_pk: int | None) -> PurchasePanelState | None:
    if not panel_code:
        return None
    panel = PANEL_REGISTRY.get(panel_code)
    if not panel:
        return None
    purchase = None
    if purchase_pk:
        queryset = PurchaseRequest.objects.all()
        if panel.code == 'reception':
            queryset = queryset.prefetch_related('items', 'reception_attachments', 'support_attachments')
        if panel.code in {'invoice', 'accounting'}:
            queryset = queryset.select_related(
                'supplier',
                'expense_type',
                'requester',
                'support_document_type',
                'scope_farm',
                'scope_chicken_house__farm',
            ).prefetch_related('items', 'support_attachments')
        if panel.code == 'accounting':
            queryset = queryset.prefetch_related('reception_attachments', 'approvals__approver')
        purchase = queryset.filter(pk=purchase_pk).first()
    return PurchasePanelState(panel=panel, purchase=purchase)


def _recent_activity() -> Sequence[dict[str, str]]:
    events = []
    qs = PurchaseRequest.objects.select_related('requester').order_by('-updated_at')[:5]
    for purchase in qs:
        actor = purchase.requester.get_full_name() if purchase.requester else "Sistema"
        events.append(
            {
                'actor': actor or "Sistema",
                'event': f"{purchase.timeline_code} · {purchase.get_status_display()}",
                'timestamp': timezone.localtime(purchase.updated_at).strftime("%d %b %H:%M"),
            }
        )
    return events
