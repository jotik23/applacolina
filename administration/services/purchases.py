from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Literal, Sequence

# Allowed status values for UI pills and stage indicators.
StageStatus = Literal['pending', 'active', 'completed', 'locked']


@dataclass(frozen=True)
class StageIndicator:
    """Represents the consolidated status of a purchase stage."""

    code: str
    label: str
    status: StageStatus
    tooltip: str


@dataclass(frozen=True)
class PurchaseAction:
    """Action rendered in the table for progressing a purchase."""

    label: str
    panel: str
    verb: str


@dataclass(frozen=True)
class PurchaseRecord:
    """Data required by the dashboard table."""

    pk: int
    timeline_code: str
    requester: str
    supplier: str
    scope_label: str
    lifecycle: str
    created_on: date
    eta: date | None
    currency: str
    total_amount: Decimal
    stage_indicators: Sequence[StageIndicator]
    action: PurchaseAction
    status_badge: str
    status_palette: str
    description: str

    @property
    def total_display(self) -> str:
        return f'{self.currency} {self.total_amount:,.2f}'


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
    purchase: PurchaseRecord | None


@dataclass(frozen=True)
class PurchaseDashboardState:
    scope: PurchaseScope
    scopes: Sequence[PurchaseScope]
    purchases: Sequence[PurchaseRecord]
    panel: PurchasePanelState | None
    recent_activity: Sequence[dict]


PANEL_REGISTRY = {
    'request': PurchasePanel('request', 'Nueva solicitud de compra', 'administration/purchases/forms/_form_request.html'),
    'order': PurchasePanel('order', 'Orden de compra', 'administration/purchases/forms/_form_order.html'),
    'reception': PurchasePanel('reception', 'Registrar recepción', 'administration/purchases/forms/_form_reception.html'),
    'invoice': PurchasePanel('invoice', 'Registrar factura', 'administration/purchases/forms/_form_invoice.html'),
    'payment': PurchasePanel('payment', 'Registrar pago', 'administration/purchases/forms/_form_payment.html'),
}


# Scope descriptors are sorted the same way the filters must be displayed.
SCOPE_DEFINITIONS = (
    ('borrador', 'Borradores', 'Solicitudes aún en preparación'),
    ('aprobacion', 'En aprobación', 'Esperando visto bueno del flujo'),
    ('ordenado', 'Orden emitida', 'Ordenes listas para recepción'),
    ('recepcion', 'Recepciones', 'Parcial o totalmente recibidas'),
    ('factura', 'Facturas', 'Documentación fiscal registrada'),
    ('pago', 'Pagos', 'Pagos programados o en curso'),
    ('archivada', 'Archivadas', 'Compras cerradas'),
)


def _stage(code: str, label: str, status: StageStatus, tooltip: str) -> StageIndicator:
    return StageIndicator(code=code, label=label, status=status, tooltip=tooltip)


PURCHASE_FIXTURE: Sequence[PurchaseRecord] = (
    PurchaseRecord(
        pk=101,
        timeline_code='SOL-2025-104',
        requester='Flavia Gómez',
        supplier='Agroinsumos del Norte',
        scope_label='Granjas · Ventilación',
        lifecycle='borrador',
        created_on=date(2025, 11, 3),
        eta=date(2025, 11, 15),
        currency='USD',
        total_amount=Decimal('18250'),
        description='Refacciones para ventiladores y repuestos eléctricos.',
        status_badge='Borrador',
        status_palette='slate',
        stage_indicators=(
            _stage('request', 'Solicitud', 'active', 'En edición por Flavia Gómez'),
            _stage('order', 'Orden', 'pending', 'Pendiente de aprobación'),
            _stage('reception', 'Recepción', 'locked', 'Necesita orden aprobada'),
            _stage('invoice', 'Factura', 'locked', 'En espera de recepción'),
            _stage('payment', 'Pago', 'locked', 'Pagos disponibles tras la factura'),
        ),
        action=PurchaseAction('Solicitar aprobación', 'request', 'solicitar_aprobacion'),
    ),
    PurchaseRecord(
        pk=102,
        timeline_code='SOL-2025-088',
        requester='Carlos Ríos',
        supplier='Servicios Frío Central',
        scope_label='Planta · Climatización',
        lifecycle='aprobacion',
        created_on=date(2025, 10, 20),
        eta=date(2025, 11, 5),
        currency='COP',
        total_amount=Decimal('14500000'),
        description='Mantenimiento correctivo urgente línea 2.',
        status_badge='En aprobación',
        status_palette='amber',
        stage_indicators=(
            _stage('request', 'Solicitud', 'completed', 'Aprobada por Operaciones'),
            _stage('order', 'Orden', 'active', 'Esperando aprobación Financiera'),
            _stage('reception', 'Recepción', 'locked', 'Orden requerida'),
            _stage('invoice', 'Factura', 'locked', 'Recepción pendiente'),
            _stage('payment', 'Pago', 'locked', 'Factura sin registrar'),
        ),
        action=PurchaseAction('Solicitar aprobación', 'request', 'solicitar_aprobacion'),
    ),
    PurchaseRecord(
        pk=103,
        timeline_code='SOL-2025-071',
        requester='Andrea Morales',
        supplier='Transporte y Logística Andina',
        scope_label='Logística · Fletes',
        lifecycle='ordenado',
        created_on=date(2025, 9, 28),
        eta=date(2025, 11, 7),
        currency='USD',
        total_amount=Decimal('8200'),
        description='Servicio de transporte nocturno granja 5.',
        status_badge='Orden emitida',
        status_palette='blue',
        stage_indicators=(
            _stage('request', 'Solicitud', 'completed', 'Flujo de aprobación completado'),
            _stage('order', 'Orden', 'completed', 'PO-4578 asignada'),
            _stage('reception', 'Recepción', 'active', 'Esperando confirmación de llegada'),
            _stage('invoice', 'Factura', 'pending', 'Factura llegará tras la recepción'),
            _stage('payment', 'Pago', 'locked', 'Factura aún no registrada'),
        ),
        action=PurchaseAction('Registrar recepción', 'reception', 'registrar_recepcion'),
    ),
    PurchaseRecord(
        pk=104,
        timeline_code='SOL-2025-055',
        requester='Jesús Pineda',
        supplier='Fertilizantes Sierra Alta',
        scope_label='Granjas · Fertilizantes',
        lifecycle='recepcion',
        created_on=date(2025, 9, 10),
        eta=None,
        currency='USD',
        total_amount=Decimal('25300'),
        description='Fertilizante foliar lote 2025-Q3.',
        status_badge='Recepción parcial',
        status_palette='violet',
        stage_indicators=(
            _stage('request', 'Solicitud', 'completed', 'Aprobada por cadena completa'),
            _stage('order', 'Orden', 'completed', 'Orden emitida y enviada'),
            _stage('reception', 'Recepción', 'active', '1 de 3 entregas registradas'),
            _stage('invoice', 'Factura', 'pending', 'Factura se espera tras recepción total'),
            _stage('payment', 'Pago', 'locked', 'Factura aún no registrada'),
        ),
        action=PurchaseAction('Registrar recepción', 'reception', 'registrar_recepcion'),
    ),
    PurchaseRecord(
        pk=105,
        timeline_code='SOL-2025-032',
        requester='Flavia Gómez',
        supplier='Soluciones Integrales IT',
        scope_label='Oficinas · Tecnología',
        lifecycle='factura',
        created_on=date(2025, 8, 3),
        eta=None,
        currency='USD',
        total_amount=Decimal('6400'),
        description='Renovación licencias ERP.',
        status_badge='Facturada',
        status_palette='emerald',
        stage_indicators=(
            _stage('request', 'Solicitud', 'completed', 'Aprobada'),
            _stage('order', 'Orden', 'completed', 'Orden interna OC-3981'),
            _stage('reception', 'Recepción', 'completed', 'Servicios recibidos'),
            _stage('invoice', 'Factura', 'active', 'Factura F-9921 en revisión'),
            _stage('payment', 'Pago', 'pending', 'Pago se habilita tras validación'),
        ),
        action=PurchaseAction('Registrar factura', 'invoice', 'registrar_factura'),
    ),
    PurchaseRecord(
        pk=106,
        timeline_code='SOL-2025-017',
        requester='Carlos Ríos',
        supplier='Transportes Pacífico',
        scope_label='Distribución · Fletes',
        lifecycle='pago',
        created_on=date(2025, 7, 12),
        eta=None,
        currency='COP',
        total_amount=Decimal('23450000'),
        description='Servicios de transporte Q2 consolidados.',
        status_badge='Pago programado',
        status_palette='cyan',
        stage_indicators=(
            _stage('request', 'Solicitud', 'completed', 'Aprobada 2025-07-15'),
            _stage('order', 'Orden', 'completed', 'Orden OC-2121'),
            _stage('reception', 'Recepción', 'completed', 'Confirmada 2025-08-02'),
            _stage('invoice', 'Factura', 'completed', 'Factura F-3215 validada'),
            _stage('payment', 'Pago', 'active', 'Pago agendado para 2025-11-10'),
        ),
        action=PurchaseAction('Registrar pago', 'payment', 'registrar_pago'),
    ),
    PurchaseRecord(
        pk=107,
        timeline_code='SOL-2025-003',
        requester='Jesús Pineda',
        supplier='Agroservicios Express',
        scope_label='Granjas · Insumos varios',
        lifecycle='archivada',
        created_on=date(2025, 1, 18),
        eta=None,
        currency='USD',
        total_amount=Decimal('4200'),
        description='Pedidos consolidados Q1',
        status_badge='Archivada',
        status_palette='slate',
        stage_indicators=(
            _stage('request', 'Solicitud', 'completed', 'Completada 2025-01-19'),
            _stage('order', 'Orden', 'completed', 'Orden OC-1001'),
            _stage('reception', 'Recepción', 'completed', 'Recibida 2025-02-02'),
            _stage('invoice', 'Factura', 'completed', 'Factura F-1201'),
            _stage('payment', 'Pago', 'completed', 'Pagada 2025-03-10'),
        ),
        action=PurchaseAction('Ver solicitud', 'request', 'ver_detalle'),
    ),
)


def _build_scopes(purchases: Iterable[PurchaseRecord]) -> Sequence[PurchaseScope]:
    counts = {code: 0 for code, *_ in SCOPE_DEFINITIONS}
    for purchase in purchases:
        counts[purchase.lifecycle] = counts.get(purchase.lifecycle, 0) + 1

    return tuple(
        PurchaseScope(code=code, label=label, description=description, count=counts.get(code, 0))
        for code, label, description in SCOPE_DEFINITIONS
    )


def _find_scope(scopes: Sequence[PurchaseScope], code: str) -> PurchaseScope:
    for scope in scopes:
        if scope.code == code:
            return scope
    return scopes[0]


def get_dashboard_state(*, scope_code: str | None, panel_code: str | None, purchase_pk: int | None) -> PurchaseDashboardState:
    scopes = _build_scopes(PURCHASE_FIXTURE)
    selected_scope = _find_scope(scopes, scope_code or scopes[0].code)
    purchases = tuple(p for p in PURCHASE_FIXTURE if p.lifecycle == selected_scope.code)
    panel_state = _resolve_panel(panel_code, purchase_pk)
    recent_activity = (
        {'actor': 'Sistema', 'event': 'Workflow registró cambio de estado', 'timestamp': 'Hace 2 min'},
        {'actor': 'Flavia Gómez', 'event': 'Adjuntó factura preliminar SOL-2025-032', 'timestamp': 'Hace 35 min'},
        {'actor': 'Carlos Ríos', 'event': 'Actualizó recepción parcial SOL-2025-088', 'timestamp': 'Ayer'},
    )
    return PurchaseDashboardState(
        scope=selected_scope,
        scopes=scopes,
        purchases=purchases,
        panel=panel_state,
        recent_activity=recent_activity,
    )


def _resolve_panel(panel_code: str | None, purchase_pk: int | None) -> PurchasePanelState | None:
    if not panel_code:
        return None
    panel = PANEL_REGISTRY.get(panel_code)
    if not panel:
        return None

    purchase = None
    if purchase_pk is not None:
        purchase = next((p for p in PURCHASE_FIXTURE if p.pk == purchase_pk), None)

    return PurchasePanelState(panel=panel, purchase=purchase)
