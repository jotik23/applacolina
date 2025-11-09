from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.functional import cached_property
from django.views import generic

from applacolina.mixins import StaffRequiredMixin
from production.models import BirdBatch, ChickenHouse, Farm

from .forms import (
    ExpenseTypeWorkflowFormSet,
    ProductForm,
    PurchasingExpenseTypeForm,
    SupplierForm,
    SupportDocumentTypeForm,
)
from .models import (
    ExpenseTypeApprovalRule,
    Product,
    PurchaseApproval,
    PurchaseRequest,
    PurchasingExpenseType,
    Supplier,
    SupportDocumentType,
)
from .services.purchase_orders import (
    PurchaseOrderPayload,
    PurchaseOrderService,
    PurchaseOrderValidationError,
)
from .services.purchase_accounting import (
    PurchaseAccountingPayload,
    PurchaseAccountingService,
    PurchaseAccountingValidationError,
)
from .services.purchase_invoices import (
    PurchaseInvoicePayload,
    PurchaseInvoiceService,
    PurchaseInvoiceValidationError,
)
from .services.purchase_payments import (
    PurchasePaymentPayload,
    PurchasePaymentService,
    PurchasePaymentValidationError,
)
from .services.purchase_receptions import (
    PurchaseReceptionPayload,
    PurchaseReceptionService,
    PurchaseReceptionValidationError,
    ReceptionItemPayload,
)
from .services.purchase_requests import (
    PurchaseItemPayload,
    PurchaseRequestPayload,
    PurchaseRequestSubmissionService,
    PurchaseRequestValidationError,
)
from .services.purchases import get_dashboard_state
from .services.workflows import (
    ExpenseTypeWorkflowRefreshService,
    PurchaseApprovalDecisionError,
    PurchaseApprovalDecisionService,
)


class AdministrationHomeView(StaffRequiredMixin, generic.TemplateView):
    template_name = 'administration/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault('administration_active_submenu', 'purchases')
        scope_override = kwargs.get('scope_override')
        panel_override = kwargs.get('panel_override')
        purchase_pk_override = kwargs.get('purchase_pk_override')
        search_query = (self.request.GET.get('search') or self.request.POST.get('search') or '').strip()
        start_date_raw = (self.request.GET.get('start_date') or self.request.POST.get('start_date') or '').strip()
        end_date_raw = (self.request.GET.get('end_date') or self.request.POST.get('end_date') or '').strip()
        page_number_raw = self.request.GET.get('page') or self.request.POST.get('page')
        page_number = _parse_int(page_number_raw) or 1
        if page_number < 1:
            page_number = 1
        start_date = parse_date(start_date_raw)
        end_date = parse_date(end_date_raw)
        state = get_dashboard_state(
            scope_code=scope_override or self.request.GET.get('scope'),
            panel_code=panel_override or self.request.GET.get('panel'),
            purchase_pk=purchase_pk_override or _parse_int(self.request.GET.get('purchase')),
            search_query=search_query or None,
            start_date=start_date,
            end_date=end_date,
            page_number=page_number,
        )
        context.update(
            purchases_scope=state.scope,
            purchases_scopes=state.scopes,
            purchases_list=state.pagination.records,
            purchases_page=state.pagination,
            purchases_panel=state.panel,
            purchases_recent_activity=state.recent_activity,
            purchases_search=search_query,
            purchases_start_date=start_date_raw,
            purchases_end_date=end_date_raw,
        )
        field_errors = kwargs.get('purchase_request_field_errors') or {}
        item_errors = kwargs.get('purchase_request_item_errors') or {}
        overrides = kwargs.get('purchase_form_overrides')
        should_build_form = overrides or (state.panel and state.panel.panel.code == 'request')
        if should_build_form:
            context.update(
                self._build_purchase_request_form_context(
                    panel_state=state.panel,
                    overrides=overrides,
                    field_errors=field_errors,
                    item_errors=item_errors,
                )
            )
        else:
            context.setdefault('purchase_request_field_errors', {})
            context.setdefault('purchase_request_item_errors', {})
        order_field_errors = kwargs.get('purchase_order_field_errors') or {}
        order_overrides = kwargs.get('purchase_order_overrides')
        should_build_order_form = order_overrides or order_field_errors or (
            state.panel and state.panel.panel.code == 'order'
        )
        if should_build_order_form:
            context.update(
                self._build_purchase_order_form_context(
                    panel_state=state.panel,
                    overrides=order_overrides,
                    field_errors=order_field_errors,
                )
            )
        else:
            context.setdefault('purchase_order_field_errors', {})
        reception_field_errors = kwargs.get('purchase_reception_field_errors') or {}
        reception_item_errors = kwargs.get('purchase_reception_item_errors') or {}
        reception_overrides = kwargs.get('purchase_reception_overrides')
        should_build_reception_form = (
            reception_overrides
            or reception_field_errors
            or reception_item_errors
            or (state.panel and state.panel.panel.code == 'reception')
        )
        if should_build_reception_form:
            context.update(
                self._build_purchase_reception_form_context(
                    panel_state=state.panel,
                    overrides=reception_overrides,
                    field_errors=reception_field_errors,
                    item_errors=reception_item_errors,
                )
            )
        else:
            context.setdefault('purchase_reception_field_errors', {})
            context.setdefault('purchase_reception_item_errors', {})
        invoice_field_errors = kwargs.get('purchase_invoice_field_errors') or {}
        invoice_overrides = kwargs.get('purchase_invoice_overrides')
        should_build_invoice_form = (
            invoice_overrides
            or invoice_field_errors
            or (state.panel and state.panel.panel.code == 'invoice')
        )
        if should_build_invoice_form:
            context.update(
                self._build_purchase_invoice_form_context(
                    panel_state=state.panel,
                    overrides=invoice_overrides,
                    field_errors=invoice_field_errors,
                )
            )
        else:
            context.setdefault('purchase_invoice_field_errors', {})
        payment_field_errors = kwargs.get('purchase_payment_field_errors') or {}
        payment_overrides = kwargs.get('purchase_payment_overrides')
        should_build_payment_form = (
            payment_overrides
            or payment_field_errors
            or (state.panel and state.panel.panel.code == 'payment')
        )
        if should_build_payment_form:
            context.update(
                self._build_purchase_payment_form_context(
                    panel_state=state.panel,
                    overrides=payment_overrides,
                    field_errors=payment_field_errors,
                )
            )
        else:
            context.setdefault('purchase_payment_field_errors', {})
        return context

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        panel = request.POST.get('panel')
        if panel == 'request':
            return self._handle_request_panel_post()
        if panel == 'order':
            return self._handle_order_panel_post()
        if panel == 'reception':
            return self._handle_reception_panel_post()
        if panel == 'invoice':
            return self._handle_invoice_panel_post()
        if panel == 'payment':
            return self._handle_payment_panel_post()
        if panel == 'accounting':
            return self._handle_accounting_panel_post()
        messages.error(request, "El formulario enviado no está disponible todavía.")
        return redirect(self._build_base_url(scope=request.POST.get('scope')))

    def _handle_request_panel_post(self) -> HttpResponse:
        intent = self.request.POST.get('intent') or 'save_draft'
        scope_code = self.request.POST.get('scope') or PurchaseRequest.Status.DRAFT
        purchase_id = _parse_int(self.request.POST.get('purchase'))
        if intent == 'reopen_request':
            return self._reopen_purchase_request(purchase_id=purchase_id)
        if intent in {'approve_request', 'reject_request'}:
            return self._handle_approval_decision(
                purchase_id=purchase_id,
                decision=intent,
            )
        payload, overrides, field_errors, item_errors = self._build_submission_payload(purchase_id=purchase_id)
        if field_errors or item_errors or payload is None:
            return self._render_request_form_errors(
                scope=scope_code,
                purchase_id=purchase_id,
                overrides=overrides,
                field_errors=field_errors,
                item_errors=item_errors,
            )

        service = PurchaseRequestSubmissionService(actor=self.request.user)
        try:
            purchase = service.submit(payload=payload, intent=intent)
        except PurchaseRequestValidationError as exc:
            self._merge_field_errors(field_errors, exc.field_errors)
            self._merge_item_errors(item_errors, exc.item_errors)
            return self._render_request_form_errors(
                scope=scope_code,
                purchase_id=purchase_id,
                overrides=overrides,
                field_errors=field_errors,
                item_errors=item_errors,
            )

        if intent == 'send_workflow':
            messages.success(self.request, "Solicitud enviada a aprobación.")
        else:
            messages.success(self.request, "Solicitud guardada en borrador.")
        return redirect(self._build_base_url(scope=purchase.status))

    def _handle_order_panel_post(self) -> HttpResponse:
        intent = self.request.POST.get('intent') or 'save_order'
        purchase_id = _parse_int(self.request.POST.get('purchase'))
        if intent == 'reopen_request':
            return self._reopen_purchase_request(purchase_id=purchase_id)
        payload, overrides, field_errors = self._build_order_payload(purchase_id=purchase_id)
        if field_errors or payload is None:
            return self._render_order_form_errors(
                scope=self.request.POST.get('scope'),
                purchase_id=purchase_id,
                overrides=overrides,
                field_errors=field_errors,
            )
        service = PurchaseOrderService(actor=self.request.user)
        try:
            purchase = service.save(payload=payload, intent=intent)
        except PurchaseOrderValidationError as exc:
            self._merge_field_errors(field_errors, exc.field_errors)
            return self._render_order_form_errors(
                scope=self.request.POST.get('scope'),
                purchase_id=purchase_id,
                overrides=overrides,
                field_errors=field_errors,
            )
        if intent == 'confirm_order':
            messages.success(self.request, "Compra gestionada. Continúa con la recepción cuando corresponda.")
        else:
            messages.success(self.request, "Información de compra guardada.")
        return redirect(self._build_base_url(scope=purchase.status))

    def _handle_reception_panel_post(self) -> HttpResponse:
        intent = self.request.POST.get('intent') or 'save_reception'
        purchase_id = _parse_int(self.request.POST.get('purchase'))
        payload, overrides, field_errors, item_errors = self._build_reception_payload(purchase_id=purchase_id)
        if field_errors or item_errors or payload is None:
            return self._render_reception_form_errors(
                scope=self.request.POST.get('scope'),
                purchase_id=purchase_id,
                overrides=overrides,
                field_errors=field_errors,
                item_errors=item_errors,
            )

        files = self.request.FILES.getlist('reception_attachments')
        service = PurchaseReceptionService(actor=self.request.user)
        try:
            purchase = service.register(payload=payload, intent=intent, attachments=files)
        except PurchaseReceptionValidationError as exc:
            self._merge_field_errors(field_errors, exc.field_errors)
            item_errors = exc.item_errors or item_errors
            return self._render_reception_form_errors(
                scope=self.request.POST.get('scope'),
                purchase_id=purchase_id,
                overrides=overrides,
                field_errors=field_errors,
                item_errors=item_errors,
        )

        if intent == 'confirm_reception':
            messages.success(self.request, "Recepción registrada. Continúa con la facturación.")
            return redirect(self._build_base_url(scope=purchase.status))
        messages.success(self.request, "Recepción guardada.")
        return redirect(
            self._build_base_url(
                scope=purchase.status,
                extra={
                    'panel': 'reception',
                    'purchase': purchase.pk,
                },
            )
        )

    def _handle_invoice_panel_post(self) -> HttpResponse:
        intent = self.request.POST.get('intent') or 'save_invoice'
        purchase_id = _parse_int(self.request.POST.get('purchase'))
        payload, overrides, field_errors = self._build_invoice_payload(purchase_id=purchase_id)
        if field_errors or payload is None:
            return self._render_invoice_form_errors(
                scope=self.request.POST.get('scope'),
                purchase_id=purchase_id,
                overrides=overrides,
                field_errors=field_errors,
            )

        files = self.request.FILES.getlist('invoice_attachments')
        service = PurchaseInvoiceService(actor=self.request.user)
        try:
            purchase = service.save(payload=payload, intent=intent, attachments=files)
        except PurchaseInvoiceValidationError as exc:
            self._merge_field_errors(field_errors, exc.field_errors)
            return self._render_invoice_form_errors(
                scope=self.request.POST.get('scope'),
                purchase_id=purchase_id,
                overrides=overrides,
                field_errors=field_errors,
            )

        if intent == 'confirm_invoice':
            messages.success(self.request, "Soporte listo y enviado a contabilidad.")
            return redirect(self._build_base_url(scope=purchase.status))
        messages.success(self.request, "Soporte actualizado.")
        return redirect(
            self._build_base_url(
                scope=purchase.status,
                extra={
                    'panel': 'invoice',
                    'purchase': purchase.pk,
                },
            )
        )

    def _handle_payment_panel_post(self) -> HttpResponse:
        intent = self.request.POST.get('intent') or 'save_payment'
        purchase_id = _parse_int(self.request.POST.get('purchase'))
        if intent == 'reopen_request':
            return self._reopen_purchase_request(purchase_id=purchase_id)
        payload, overrides, field_errors = self._build_payment_payload(purchase_id=purchase_id, intent=intent)
        if field_errors or payload is None:
            return self._render_payment_form_errors(
                scope=self.request.POST.get('scope'),
                purchase_id=purchase_id,
                overrides=overrides,
                field_errors=field_errors,
            )
        service = PurchasePaymentService(actor=self.request.user)
        try:
            purchase = service.save(payload=payload, intent=intent)
        except PurchasePaymentValidationError as exc:
            self._merge_field_errors(field_errors, exc.field_errors)
            return self._render_payment_form_errors(
                scope=self.request.POST.get('scope'),
                purchase_id=purchase_id,
                overrides=overrides,
                field_errors=field_errors,
            )
        if intent == 'confirm_payment':
            messages.success(self.request, "Pago registrado. Continúa adjuntando los soportes.")
        else:
            messages.success(self.request, "Información de pago guardada.")
        return redirect(self._build_base_url(scope=purchase.status))

    def _handle_accounting_panel_post(self) -> HttpResponse:
        scope = self.request.POST.get('scope') or PurchaseRequest.Status.PAYMENT
        purchase_id = _parse_int(self.request.POST.get('purchase'))
        intent = self.request.POST.get('intent') or 'close_panel'
        if intent != 'confirm_accounting':
            return redirect(self._build_base_url(scope=scope))
        if not purchase_id:
            messages.error(self.request, "Selecciona la solicitud que deseas contabilizar.")
            return redirect(self._build_base_url(scope=scope))
        service = PurchaseAccountingService(actor=self.request.user)
        payload = PurchaseAccountingPayload(purchase_id=purchase_id)
        try:
            purchase = service.mark_accounted(payload=payload)
        except PurchaseAccountingValidationError as exc:
            messages.error(self.request, self._first_error_message(exc.field_errors))
            return redirect(
                self._build_base_url(
                    scope=scope,
                    extra={
                        'panel': 'accounting',
                        'purchase': purchase_id,
                    },
                )
            )
        messages.success(self.request, "Compra contabilizada y archivada.")
        return redirect(self._build_base_url(scope=purchase.status))

    def _render_request_form_errors(
        self,
        *,
        scope: str | None,
        purchase_id: int | None,
        overrides: dict,
        field_errors: dict[str, list[str]],
        item_errors: dict[int, dict[str, list[str]]],
    ) -> HttpResponse:
        return self.render_to_response(
            self.get_context_data(
                scope_override=scope,
                panel_override='request',
                purchase_pk_override=purchase_id,
                purchase_form_overrides=overrides,
                purchase_request_field_errors=field_errors,
                purchase_request_item_errors=item_errors,
            )
        )

    def _render_order_form_errors(
        self,
        *,
        scope: str | None,
        purchase_id: int | None,
        overrides: dict,
        field_errors: dict[str, list[str]],
    ) -> HttpResponse:
        return self.render_to_response(
            self.get_context_data(
                scope_override=scope,
                panel_override='order',
                purchase_pk_override=purchase_id,
                purchase_order_overrides=overrides,
                purchase_order_field_errors=field_errors,
            )
        )

    def _render_reception_form_errors(
        self,
        *,
        scope: str | None,
        purchase_id: int | None,
        overrides: dict,
        field_errors: dict[str, list[str]],
        item_errors: dict[int, list[str]],
    ) -> HttpResponse:
        return self.render_to_response(
            self.get_context_data(
                scope_override=scope,
                panel_override='reception',
                purchase_pk_override=purchase_id,
                purchase_reception_overrides=overrides,
                purchase_reception_field_errors=field_errors,
                purchase_reception_item_errors=item_errors,
            )
        )

    def _render_invoice_form_errors(
        self,
        *,
        scope: str | None,
        purchase_id: int | None,
        overrides: dict,
        field_errors: dict[str, list[str]],
    ) -> HttpResponse:
        return self.render_to_response(
            self.get_context_data(
                scope_override=scope,
                panel_override='invoice',
                purchase_pk_override=purchase_id,
                purchase_invoice_overrides=overrides,
                purchase_invoice_field_errors=field_errors,
            )
        )

    def _render_payment_form_errors(
        self,
        *,
        scope: str | None,
        purchase_id: int | None,
        overrides: dict,
        field_errors: dict[str, list[str]],
    ) -> HttpResponse:
        return self.render_to_response(
            self.get_context_data(
                scope_override=scope,
                panel_override='payment',
                purchase_pk_override=purchase_id,
                purchase_payment_overrides=overrides,
                purchase_payment_field_errors=field_errors,
            )
        )

    def _reopen_purchase_request(self, *, purchase_id: int | None) -> HttpResponse:
        if not purchase_id:
            messages.error(self.request, "No encontramos la solicitud que deseas modificar.")
            return redirect(self._build_base_url(scope=self.request.POST.get('scope')))
        purchase = PurchaseRequest.objects.filter(pk=purchase_id).first()
        if not purchase:
            messages.error(self.request, "La solicitud seleccionada ya no existe.")
            return redirect(self._build_base_url(scope=self.request.POST.get('scope')))
        purchase.status = PurchaseRequest.Status.DRAFT
        purchase.save(update_fields=['status', 'updated_at'])
        messages.info(self.request, "La solicitud volvió a borrador y debe aprobarse nuevamente.")
        return redirect(
            self._build_base_url(
                scope=purchase.status,
                extra={'panel': 'request', 'purchase': purchase.pk},
            )
        )

    def _handle_approval_decision(self, *, purchase_id: int | None, decision: str) -> HttpResponse:
        scope = self.request.POST.get('scope') or PurchaseRequest.Status.SUBMITTED
        if not purchase_id:
            messages.error(self.request, "Selecciona la solicitud que deseas aprobar.")
            return redirect(self._build_base_url(scope=scope))
        purchase = PurchaseRequest.objects.filter(pk=purchase_id).first()
        if not purchase:
            messages.error(self.request, "La solicitud seleccionada ya no existe.")
            return redirect(self._build_base_url(scope=scope))
        note = (self.request.POST.get('approval_note') or '').strip()
        service = PurchaseApprovalDecisionService(
            purchase_request=purchase,
            actor=self.request.user,
        )
        try:
            if decision == 'approve_request':
                result = service.approve(note=note)
            else:
                result = service.reject(note=note)
        except PurchaseApprovalDecisionError as exc:
            messages.error(self.request, str(exc))
            return redirect(
                self._build_base_url(
                    scope=purchase.status,
                    extra={'panel': 'request', 'purchase': purchase.pk},
                )
            )

        purchase.refresh_from_db()
        if result.decision == 'approved':
            if result.workflow_completed:
                messages.success(self.request, "Solicitud aprobada completamente.")
            else:
                messages.success(
                    self.request,
                    "Tu aprobación fue registrada. El flujo continuará con el siguiente aprobador.",
                )
        else:
            messages.warning(self.request, "Solicitud rechazada y devuelta a borrador.")

        return redirect(self._build_base_url(scope=PurchaseRequest.Status.SUBMITTED))

    def _build_submission_payload(
        self,
        *,
        purchase_id: int | None,
    ) -> tuple[PurchaseRequestPayload | None, dict, dict[str, list[str]], dict[int, dict[str, list[str]]]]:
        summary = (self.request.POST.get('summary') or '').strip()
        notes = (self.request.POST.get('notes') or '').strip()
        expense_type_id = _parse_int(self.request.POST.get('expense_type'))
        support_document_type_id = _parse_int(self.request.POST.get('support_document_type'))
        supplier_id = _parse_int(self.request.POST.get('supplier'))
        raw_scope = {
            'farm_id': _parse_int(self.request.POST.get('scope_farm_id')),
            'chicken_house_id': _parse_int(self.request.POST.get('scope_chicken_house_id')),
            'batch_code': (self.request.POST.get('scope_batch_code') or '').strip(),
        }
        items_raw = self._enrich_item_rows(self._extract_item_rows())
        overrides = {
            'summary': summary,
            'notes': notes,
            'expense_type_id': expense_type_id,
            'support_document_type_id': support_document_type_id,
            'supplier_id': supplier_id,
            'items': items_raw,
        }
        field_errors: dict[str, list[str]] = {}
        item_errors: dict[int, dict[str, list[str]]] = {}

        scope_area_raw = (self.request.POST.get('scope_area') or '').strip()
        area_selection = self._normalize_scope_area_selection(
            raw_value=scope_area_raw,
            farm_id=raw_scope['farm_id'],
            chicken_house_id=raw_scope['chicken_house_id'],
        )
        scope_values = {
            'farm_id': area_selection['farm_id'],
            'chicken_house_id': area_selection['chicken_house_id'],
            'batch_code': raw_scope['batch_code'],
        }
        overrides['scope_values'] = scope_values
        overrides['scope_area'] = area_selection['value']
        if area_selection['error']:
            field_errors['scope_area'] = [area_selection['error']]

        item_payloads: list[PurchaseItemPayload] = []
        for index, row in enumerate(items_raw):
            row_errors: dict[str, list[str]] = {}
            description = (row.get('description') or '').strip()
            product_id = _parse_int(row.get('product_id'))
            if not description and not product_id:
                row_errors['description'] = ["Selecciona un producto o describe el item."]

            quantity = self._parse_decimal(row.get('quantity'), allow_empty=False)
            if quantity is None:
                row_errors.setdefault('quantity', []).append("Ingresa una cantidad válida.")
            estimated_amount = self._parse_decimal(row.get('estimated_amount'), allow_empty=True)
            if estimated_amount is None:
                row_errors.setdefault('estimated_amount', []).append("Ingresa un monto estimado válido.")

            item_id = _parse_int(row.get('id'))

            if row_errors:
                item_errors[index] = row_errors
                continue

            item_payloads.append(
                PurchaseItemPayload(
                    id=item_id,
                    description=description,
                    quantity=quantity or Decimal('0'),
                    estimated_amount=estimated_amount or Decimal('0'),
                    product_id=product_id,
                )
            )

        if not items_raw:
            field_errors['items'] = ["Agrega al menos un item a la solicitud."]

        payload = None
        if not field_errors and not item_errors:
            payload = PurchaseRequestPayload(
                purchase_id=purchase_id,
                summary=summary,
                notes=notes,
                expense_type_id=expense_type_id,
                support_document_type_id=support_document_type_id,
                supplier_id=supplier_id,
                items=item_payloads,
                scope_farm_id=scope_values['farm_id'],
                scope_chicken_house_id=scope_values['chicken_house_id'],
                scope_batch_code=scope_values['batch_code'],
                scope_area=area_selection['kind'],
            )
        return payload, overrides, field_errors, item_errors

    def _build_order_payload(
        self,
        *,
        purchase_id: int | None,
    ) -> tuple[PurchaseOrderPayload | None, dict, dict[str, list[str]]]:
        purchase_date_raw = (self.request.POST.get('purchase_date') or '').strip()
        delivery_condition = (self.request.POST.get('delivery_condition') or '').strip()
        shipping_eta_raw = (self.request.POST.get('shipping_eta') or '').strip()
        shipping_notes = (self.request.POST.get('shipping_notes') or '').strip()
        payment_condition = (self.request.POST.get('payment_condition') or '').strip()
        payment_method = (self.request.POST.get('payment_method') or '').strip()
        supplier_account_holder_id = (self.request.POST.get('supplier_account_holder_id') or '').strip()
        supplier_account_holder_name = (self.request.POST.get('supplier_account_holder_name') or '').strip()
        supplier_account_type = (self.request.POST.get('supplier_account_type') or '').strip()
        supplier_account_number = (self.request.POST.get('supplier_account_number') or '').strip()
        supplier_bank_name = (self.request.POST.get('supplier_bank_name') or '').strip()
        purchase_date = self._parse_date(purchase_date_raw)
        shipping_eta = self._parse_date(shipping_eta_raw)
        overrides = {
            'purchase_date': purchase_date_raw,
            'delivery_condition': delivery_condition,
            'shipping_eta': shipping_eta_raw,
            'shipping_notes': shipping_notes,
            'payment_condition': payment_condition,
            'payment_method': payment_method,
            'supplier_account_holder_id': supplier_account_holder_id,
            'supplier_account_holder_name': supplier_account_holder_name,
            'supplier_account_type': supplier_account_type,
            'supplier_account_number': supplier_account_number,
            'supplier_bank_name': supplier_bank_name,
        }
        field_errors: dict[str, list[str]] = {}
        if not purchase_id:
            field_errors.setdefault('non_field', []).append("Selecciona una solicitud para gestionar.")
        if not purchase_date_raw:
            field_errors.setdefault('purchase_date', []).append("Selecciona la fecha de compra.")
        elif purchase_date is None:
            field_errors.setdefault('purchase_date', []).append("Ingresa una fecha válida (AAAA-MM-DD).")
        delivery_options = set(PurchaseRequest.DeliveryCondition.values)
        if delivery_condition and delivery_condition not in delivery_options:
            field_errors.setdefault('delivery_condition', []).append("Selecciona una entrega válida.")
        if not delivery_condition:
            delivery_condition = PurchaseRequest.DeliveryCondition.IMMEDIATE
        if delivery_condition == PurchaseRequest.DeliveryCondition.SHIPPING:
            if not shipping_eta_raw:
                field_errors.setdefault('shipping_eta', []).append("Ingresa la fecha estimada de llegada.")
            elif shipping_eta is None:
                field_errors.setdefault('shipping_eta', []).append("Fecha estimada inválida.")
        allowed_conditions = set(PurchaseRequest.PaymentCondition.values)
        if payment_condition and payment_condition not in allowed_conditions:
            field_errors.setdefault('payment_condition', []).append("Selecciona una opción válida.")
        if not payment_condition:
            field_errors.setdefault('payment_condition', []).append("Selecciona una condición de pago.")
        allowed_methods = set(PurchaseRequest.PaymentMethod.values)
        if payment_method and payment_method not in allowed_methods:
            field_errors.setdefault('payment_method', []).append("Selecciona un medio de pago válido.")
        if not payment_method:
            field_errors.setdefault('payment_method', []).append("Selecciona un medio de pago.")
        require_bank_data = payment_method == PurchaseRequest.PaymentMethod.TRANSFER
        account_types = {choice for choice, _ in Supplier.ACCOUNT_TYPE_CHOICES}
        if require_bank_data:
            if supplier_account_type and supplier_account_type not in account_types:
                field_errors.setdefault('supplier_account_type', []).append("Selecciona un tipo de cuenta válido.")
            if not supplier_account_type:
                field_errors.setdefault('supplier_account_type', []).append("Selecciona el tipo de cuenta.")
            if not supplier_account_holder_name:
                field_errors.setdefault('supplier_account_holder_name', []).append("Ingresa el titular de la cuenta.")
            if not supplier_account_holder_id:
                field_errors.setdefault('supplier_account_holder_id', []).append("Ingresa la identificación del titular.")
            if not supplier_account_number:
                field_errors.setdefault('supplier_account_number', []).append("Ingresa el número de cuenta.")
            if not supplier_bank_name:
                field_errors.setdefault('supplier_bank_name', []).append("Ingresa el banco.")
        else:
            # Allow keeping existing values but do not enforce them.
            if supplier_account_type and supplier_account_type not in account_types:
                field_errors.setdefault('supplier_account_type', []).append("Selecciona un tipo de cuenta válido.")
        payload = None
        if not field_errors and purchase_id:
            payload = PurchaseOrderPayload(
                purchase_id=purchase_id,
                purchase_date=purchase_date or timezone.localdate(),
                delivery_condition=delivery_condition,
                shipping_eta=shipping_eta if delivery_condition == PurchaseRequest.DeliveryCondition.SHIPPING else None,
                shipping_notes=shipping_notes if delivery_condition == PurchaseRequest.DeliveryCondition.SHIPPING else '',
                payment_condition=payment_condition,
                payment_method=payment_method,
                supplier_account_holder_id=supplier_account_holder_id,
                supplier_account_holder_name=supplier_account_holder_name,
                supplier_account_type=supplier_account_type,
                supplier_account_number=supplier_account_number,
                supplier_bank_name=supplier_bank_name,
            )
        return payload, overrides, field_errors

    def _build_reception_payload(
        self,
        *,
        purchase_id: int | None,
    ) -> tuple[PurchaseReceptionPayload | None, dict, dict[str, list[str]], dict[int, list[str]]]:
        notes = (self.request.POST.get('reception_notes') or '').strip()
        rows = self._extract_reception_rows()
        overrides = {
            'notes': notes,
            'items': rows,
        }
        field_errors: dict[str, list[str]] = {}
        item_errors: dict[int, list[str]] = {}
        payload_items: list[ReceptionItemPayload] = []
        for index, row in enumerate(rows):
            item_id = _parse_int(row.get('item_id'))
            received = self._parse_decimal(row.get('received_quantity'), allow_empty=False)
            errors: list[str] = []
            if not item_id:
                errors.append("Item inválido.")
            if received is None:
                errors.append("Ingresa una cantidad válida.")
            elif received < 0:
                errors.append("La cantidad no puede ser negativa.")
            if errors:
                item_errors[index] = errors
                continue
            payload_items.append(
                ReceptionItemPayload(
                    item_id=item_id,
                    received_quantity=received,
                )
            )
        payload = None
        if purchase_id and not field_errors and not item_errors:
            payload = PurchaseReceptionPayload(
                purchase_id=purchase_id,
                notes=notes,
                items=payload_items,
            )
        return payload, overrides, field_errors, item_errors

    def _build_payment_payload(
        self,
        *,
        purchase_id: int | None,
        intent: str,
    ) -> tuple[PurchasePaymentPayload | None, dict, dict[str, list[str]]]:
        payment_amount_raw = (self.request.POST.get('payment_amount') or '').strip()
        payment_method = (self.request.POST.get('payment_method') or '').strip()
        payment_condition = (self.request.POST.get('payment_condition') or '').strip()
        payment_source = (self.request.POST.get('payment_source') or '').strip()
        payment_notes = (self.request.POST.get('payment_notes') or '').strip()
        supplier_account_holder_id = (self.request.POST.get('supplier_account_holder_id') or '').strip()
        supplier_account_holder_name = (self.request.POST.get('supplier_account_holder_name') or '').strip()
        supplier_account_type = (self.request.POST.get('supplier_account_type') or '').strip()
        supplier_account_number = (self.request.POST.get('supplier_account_number') or '').strip()
        supplier_bank_name = (self.request.POST.get('supplier_bank_name') or '').strip()
        overrides = {
            'payment_amount': payment_amount_raw,
            'payment_method': payment_method,
            'payment_condition': payment_condition,
            'payment_source': payment_source,
            'payment_notes': payment_notes,
            'supplier_account_holder_id': supplier_account_holder_id,
            'supplier_account_holder_name': supplier_account_holder_name,
            'supplier_account_type': supplier_account_type,
            'supplier_account_number': supplier_account_number,
            'supplier_bank_name': supplier_bank_name,
        }
        field_errors: dict[str, list[str]] = {}
        payment_amount = self._parse_decimal(payment_amount_raw, allow_empty=False)
        if payment_amount is None:
            field_errors.setdefault('payment_amount', []).append("Ingresa un monto válido.")
        elif payment_amount <= Decimal('0'):
            field_errors.setdefault('payment_amount', []).append("El monto debe ser mayor que cero.")
        if not purchase_id:
            field_errors.setdefault('non_field', []).append("Selecciona una solicitud para registrar el pago.")
        allowed_conditions = set(PurchaseRequest.PaymentCondition.values)
        if payment_condition and payment_condition not in allowed_conditions:
            field_errors.setdefault('payment_condition', []).append("Selecciona una condición de pago válida.")
        if not payment_condition:
            field_errors.setdefault('payment_condition', []).append("Selecciona una condición de pago.")
        allowed_methods = set(PurchaseRequest.PaymentMethod.values)
        if payment_method and payment_method not in allowed_methods:
            field_errors.setdefault('payment_method', []).append("Selecciona un medio de pago válido.")
        if not payment_method:
            field_errors.setdefault('payment_method', []).append("Selecciona un medio de pago.")
        payment_sources = set(PurchaseRequest.PaymentSource.values)
        if payment_source and payment_source not in payment_sources:
            field_errors.setdefault('payment_source', []).append("Selecciona el origen del pago.")
        if not payment_source:
            payment_source = PurchaseRequest.PaymentSource.TBD
        account_types = {choice for choice, _ in Supplier.ACCOUNT_TYPE_CHOICES}
        require_bank_data = payment_method == PurchaseRequest.PaymentMethod.TRANSFER
        if require_bank_data:
            if supplier_account_type and supplier_account_type not in account_types:
                field_errors.setdefault('supplier_account_type', []).append("Selecciona un tipo de cuenta válido.")
            if not supplier_account_type:
                field_errors.setdefault('supplier_account_type', []).append("Selecciona el tipo de cuenta.")
            if not supplier_account_holder_name:
                field_errors.setdefault('supplier_account_holder_name', []).append("Ingresa el titular de la cuenta.")
            if not supplier_account_holder_id:
                field_errors.setdefault('supplier_account_holder_id', []).append("Ingresa la identificación del titular.")
            if not supplier_account_number:
                field_errors.setdefault('supplier_account_number', []).append("Ingresa el número de cuenta.")
            if not supplier_bank_name:
                field_errors.setdefault('supplier_bank_name', []).append("Ingresa el banco.")
        else:
            if supplier_account_type and supplier_account_type not in account_types:
                field_errors.setdefault('supplier_account_type', []).append("Selecciona un tipo de cuenta válido.")
        payload = None
        if purchase_id and not field_errors and payment_amount is not None:
            payload = PurchasePaymentPayload(
                purchase_id=purchase_id,
                payment_amount=payment_amount,
                payment_method=payment_method,
                payment_condition=payment_condition,
                payment_source=payment_source,
                payment_notes=payment_notes,
                supplier_account_holder_id=supplier_account_holder_id,
                supplier_account_holder_name=supplier_account_holder_name,
                supplier_account_type=supplier_account_type,
                supplier_account_number=supplier_account_number,
                supplier_bank_name=supplier_bank_name,
            )
        return payload, overrides, field_errors

    def _parse_decimal(self, value: str | None, *, allow_empty: bool) -> Decimal | None:
        if value is None or value == '':
            return Decimal('0') if allow_empty else None
        try:
            return Decimal(value)
        except (InvalidOperation, TypeError):
            return None

    def _parse_date(self, value: str | None):
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    def _extract_item_rows(self) -> list[dict[str, str]]:
        rows: dict[int, dict[str, str]] = {}
        for key in self.request.POST.keys():
            match = ITEM_KEY_PATTERN.match(key)
            if not match:
                continue
            index = int(match.group(1))
            field = match.group(2)
            rows.setdefault(index, {})[field] = self.request.POST.get(key, '').strip()
        ordered_rows: list[dict[str, str]] = []
        for index in sorted(rows.keys()):
            row = rows[index]
            significant_fields = (
                row.get('description'),
                row.get('product_id'),
                row.get('quantity'),
                row.get('estimated_amount'),
            )
            if not any(significant_fields):
                continue
            ordered_rows.append(row)
        return ordered_rows

    def _extract_reception_rows(self) -> list[dict[str, str]]:
        rows: dict[int, dict[str, str]] = {}
        for key in self.request.POST.keys():
            match = RECEPTION_ITEM_PATTERN.match(key)
            if not match:
                continue
            index = int(match.group(1))
            field = match.group(2)
            rows.setdefault(index, {})[field] = self.request.POST.get(key, '').strip()
        ordered: list[dict[str, str]] = []
        for index in sorted(rows.keys()):
            ordered.append(rows[index])
        return ordered

    def _build_purchase_request_form_context(
        self,
        *,
        panel_state,
        overrides: dict | None,
        field_errors: dict[str, list[str]],
        item_errors: dict[int, dict[str, list[str]]],
    ) -> dict:
        purchase = panel_state.purchase if panel_state else None
        form_initial = self._resolve_form_initial(purchase=purchase, overrides=overrides)
        items = form_initial['items'] or [self._blank_item_row()]
        pending_approval = None
        if (
            purchase
            and purchase.status == PurchaseRequest.Status.SUBMITTED
            and self.request.user.is_authenticated
        ):
            pending_approval = (
                purchase.approvals.filter(
                    approver=self.request.user,
                    status=PurchaseApproval.Status.PENDING,
                )
                .order_by('sequence')
                .first()
            )
        rejection_alert = None
        if purchase and purchase.status == PurchaseRequest.Status.DRAFT:
            last_rejection = (
                purchase.approvals.filter(status=PurchaseApproval.Status.REJECTED)
                .order_by('-decided_at', '-updated_at')
                .first()
            )
            if last_rejection:
                rejection_alert = {
                    'role': last_rejection.role,
                    'note': last_rejection.comments or '',
                    'decided_at': last_rejection.decided_at,
                }
        approval_note_value = ''
        if overrides and overrides.get('approval_note') is not None:
            approval_note_value = overrides.get('approval_note') or ''
        elif self.request.method == 'POST':
            approval_note_value = (self.request.POST.get('approval_note') or '').strip()
        context = {
            'purchase_request_form': {
                'categories': self.purchase_form_options['categories'],
                'support_types': self.purchase_form_options['support_types'],
                'suppliers': self.purchase_form_options['suppliers'],
                'products': self.purchase_form_options['products'],
                'farms': self.purchase_form_options['farms'],
                'chicken_houses': self.purchase_form_options['chicken_houses'],
                'bird_batches': self.purchase_form_options['bird_batches'],
                'items': items,
                'scope_values': form_initial['scope'],
                'scope_area_value': form_initial['scope_area_value'],
                'initial': form_initial['values'],
                'read_only': form_initial['read_only'],
                'category_picker': self._build_category_picker(form_initial['values'].get('expense_type_id')),
                'supplier_picker': self._build_supplier_picker(form_initial['values'].get('supplier_id')),
                'area_picker': self._build_area_picker(
                    selected_value=form_initial['scope_area_value'],
                ),
                'can_reopen': bool(purchase and purchase.status == PurchaseRequest.Status.SUBMITTED),
                'pending_approval': pending_approval,
                'approval_note': approval_note_value,
                'rejection_alert': rejection_alert,
            },
            'purchase_request_field_errors': field_errors,
            'purchase_request_item_errors': item_errors,
        }
        return context

    def _build_purchase_order_form_context(
        self,
        *,
        panel_state,
        overrides: dict | None,
        field_errors: dict[str, list[str]],
    ) -> dict:
        purchase = panel_state.purchase if panel_state else None
        supplier = purchase.supplier if purchase else None
        default_purchase_date = (purchase.purchase_date or timezone.localdate()).isoformat() if purchase else ''
        initial = {
            'purchase_date': default_purchase_date,
            'delivery_condition': purchase.delivery_condition if purchase else PurchaseRequest.DeliveryCondition.IMMEDIATE,
            'shipping_eta': purchase.shipping_eta.isoformat() if purchase and purchase.shipping_eta else '',
            'shipping_notes': purchase.shipping_notes if purchase else '',
            'payment_condition': purchase.payment_condition or PurchaseRequest.PaymentCondition.CASH,
            'payment_method': purchase.payment_method or PurchaseRequest.PaymentMethod.TRANSFER,
            'supplier_account_holder_id': purchase.supplier_account_holder_id
            or (supplier.account_holder_id if supplier else ''),
            'supplier_account_holder_name': purchase.supplier_account_holder_name
            or (supplier.account_holder_name if supplier else ''),
            'supplier_account_type': purchase.supplier_account_type
            or (supplier.account_type if supplier else ''),
            'supplier_account_number': purchase.supplier_account_number
            or (supplier.account_number if supplier else ''),
            'supplier_bank_name': purchase.supplier_bank_name or (supplier.bank_name if supplier else ''),
        }
        if overrides:
            initial.update({k: v for k, v in overrides.items() if v is not None})
        context = {
            'purchase_order_form': {
                'initial': initial,
                'payment_conditions': PurchaseRequest.PaymentCondition.choices,
                'payment_methods': PurchaseRequest.PaymentMethod.choices,
                'delivery_conditions': PurchaseRequest.DeliveryCondition.choices,
                'account_types': Supplier.ACCOUNT_TYPE_CHOICES,
                'purchase': purchase,
                'can_reopen': bool(purchase and purchase.status != PurchaseRequest.Status.DRAFT),
            },
            'purchase_order_field_errors': field_errors,
        }
        return context

    def _build_purchase_payment_form_context(
        self,
        *,
        panel_state,
        overrides: dict | None,
        field_errors: dict[str, list[str]],
    ) -> dict:
        purchase = panel_state.purchase if panel_state else None
        supplier = purchase.supplier if purchase else None
        initial = {
            'payment_method': (
                purchase.payment_method if purchase and purchase.payment_method else PurchaseRequest.PaymentMethod.TRANSFER
            ),
            'payment_condition': (
                purchase.payment_condition if purchase and purchase.payment_condition else PurchaseRequest.PaymentCondition.CASH
            ),
            'payment_source': (
                purchase.payment_source if purchase and purchase.payment_source else PurchaseRequest.PaymentSource.TBD
            ),
            'payment_notes': purchase.payment_notes if purchase and purchase.payment_notes else '',
            'supplier_account_holder_id': purchase.supplier_account_holder_id
            if purchase and purchase.supplier_account_holder_id
            else (supplier.account_holder_id if supplier else ''),
            'supplier_account_holder_name': purchase.supplier_account_holder_name
            if purchase and purchase.supplier_account_holder_name
            else (supplier.account_holder_name if supplier else ''),
            'supplier_account_type': purchase.supplier_account_type
            if purchase and purchase.supplier_account_type
            else (supplier.account_type if supplier else ''),
            'supplier_account_number': purchase.supplier_account_number
            if purchase and purchase.supplier_account_number
            else (supplier.account_number if supplier else ''),
            'supplier_bank_name': purchase.supplier_bank_name
            if purchase and purchase.supplier_bank_name
            else (supplier.bank_name if supplier else ''),
        }
        if purchase:
            baseline_amount = purchase.payment_amount or purchase.estimated_total or Decimal('0.00')
        else:
            baseline_amount = Decimal('0.00')
        initial.setdefault('payment_amount', f"{baseline_amount:.2f}")
        if overrides:
            initial.update({k: v for k, v in overrides.items() if v is not None})
        amount_exceeds_estimate = False
        if purchase:
            try:
                posted_amount = Decimal(str(initial.get('payment_amount') or '0'))
            except (InvalidOperation, TypeError):
                posted_amount = Decimal('0')
            amount_exceeds_estimate = posted_amount > (purchase.estimated_total or Decimal('0'))
        context = {
            'purchase_payment_form': {
                'initial': initial,
                'payment_conditions': PurchaseRequest.PaymentCondition.choices,
                'payment_methods': PurchaseRequest.PaymentMethod.choices,
                'payment_sources': PurchaseRequest.PaymentSource.choices,
                'account_types': Supplier.ACCOUNT_TYPE_CHOICES,
                'purchase': purchase,
                'estimated_total': purchase.estimated_total if purchase else Decimal('0.00'),
                'amount_exceeds_estimate': amount_exceeds_estimate,
            },
            'purchase_payment_field_errors': field_errors,
        }
        return context

    def _build_purchase_reception_form_context(
        self,
        *,
        panel_state,
        overrides: dict | None,
        field_errors: dict[str, list[str]],
        item_errors: dict[int, list[str]],
    ) -> dict:
        purchase = panel_state.purchase if panel_state else None
        items: list[dict[str, Decimal | int | str]] = []
        if purchase:
            for item in purchase.items.all().order_by('id'):
                items.append(
                    {
                        'id': item.id,
                        'description': item.description,
                        'requested_quantity': item.quantity,
                        'received_quantity': item.received_quantity,
                        'difference': item.received_quantity - item.quantity,
                    }
                )
        if overrides and overrides.get('items'):
            for index, row in enumerate(overrides['items']):
                item_id = _parse_int(row.get('item_id'))
                received_raw = row.get('received_quantity')
                for item in items:
                    if item['id'] == item_id and received_raw not in (None, ''):
                        try:
                            received_value = Decimal(received_raw)
                        except (InvalidOperation, TypeError):
                            continue
                        item['received_quantity'] = received_value
                        item['difference'] = received_value - item['requested_quantity']
        has_mismatch = any(item['difference'] for item in items)
        form = {
            'items': items,
            'notes': overrides.get('notes') if overrides else (purchase.reception_notes if purchase else ''),
            'attachments': purchase.reception_attachments.all() if purchase else [],
            'has_mismatch': has_mismatch,
        }
        return {
            'purchase_reception_form': form,
            'purchase_reception_field_errors': field_errors,
            'purchase_reception_item_errors': item_errors,
        }

    def _build_purchase_invoice_form_context(
        self,
        *,
        panel_state,
        overrides: dict | None,
        field_errors: dict[str, list[str]],
    ) -> dict:
        purchase = panel_state.purchase if panel_state else None
        selected_support_type = None
        if overrides and overrides.get('support_document_type_id') is not None:
            selected_support_type = overrides.get('support_document_type_id')
        elif purchase:
            selected_support_type = purchase.support_document_type_id
        selected_support_type = _parse_int(selected_support_type)
        template_values = overrides.get('template_fields') if overrides else (purchase.support_template_values if purchase else {})
        template_catalog = self._build_support_template_catalog(purchase=purchase)
        template_config = {
            'purchaseId': purchase.pk if purchase else None,
            'selectedSupportTypeId': selected_support_type,
            'supportTypes': self.purchase_form_options['support_types'],
            'fieldCatalog': template_catalog,
            'savedValues': template_values or {},
        }
        form = {
            'initial': {
                'support_document_type_id': selected_support_type,
            },
            'support_types': self.purchase_form_options['support_types'],
            'attachments': purchase.support_attachments.all() if purchase else [],
            'template_config': template_config,
        }
        return {
            'purchase_invoice_form': form,
            'purchase_invoice_field_errors': field_errors,
        }

    def _build_invoice_payload(
        self,
        *,
        purchase_id: int | None,
    ) -> tuple[PurchaseInvoicePayload | None, dict, dict[str, list[str]]]:
        field_errors: dict[str, list[str]] = {}
        template_fields = self._parse_template_fields()
        overrides = {
            'support_document_type_id': self.request.POST.get('support_document_type'),
            'template_fields': template_fields,
        }
        if not purchase_id:
            field_errors.setdefault('non_field', []).append("Selecciona una solicitud válida.")
            return None, overrides, field_errors
        payload = PurchaseInvoicePayload(
            purchase_id=purchase_id,
            support_document_type_id=_parse_int(overrides['support_document_type_id']),
            template_values=template_fields,
        )
        return payload, overrides, field_errors

    def _resolve_form_initial(self, *, purchase, overrides: dict | None) -> dict:
        read_only = bool(purchase and purchase.status != PurchaseRequest.Status.DRAFT)
        if overrides:
            values = {
                'summary': overrides.get('summary') or '',
                'notes': overrides.get('notes') or '',
                'expense_type_id': overrides.get('expense_type_id'),
                'support_document_type_id': overrides.get('support_document_type_id'),
                'supplier_id': overrides.get('supplier_id'),
                'estimated_total': overrides.get('estimated_total') or '',
            }
            scope = overrides.get('scope_values') or {'farm_id': None, 'chicken_house_id': None, 'batch_code': ''}
            items = self._enrich_item_rows(overrides.get('items') or [])
            scope_area_value = overrides.get('scope_area') or self._scope_area_value_from_scope(scope=scope, area_kind=None)
        else:
            values = {
                'summary': purchase.name if purchase else '',
                'notes': purchase.description if purchase else '',
                'expense_type_id': purchase.expense_type_id if purchase else None,
                'support_document_type_id': purchase.support_document_type_id if purchase else None,
                'supplier_id': purchase.supplier_id if purchase else None,
                'estimated_total': self._format_decimal(purchase.estimated_total) if purchase else '',
            }
            scope = self._scope_values_from_purchase(purchase)
            items = self._serialize_items(purchase)
            scope_area_value = self._scope_area_value_from_purchase(purchase)
        return {
            'values': values,
            'scope': scope,
            'scope_area_value': scope_area_value,
            'items': items,
            'read_only': read_only,
        }

    def _serialize_items(self, purchase) -> list[dict[str, str]]:
        if not purchase:
            return []
        serialized: list[dict[str, str]] = []
        for item in purchase.items.select_related('product').all():
            serialized.append(
                {
                    'id': str(item.id),
                    'description': item.description,
                    'quantity': self._format_decimal(item.quantity),
                    'estimated_amount': self._format_decimal(item.estimated_amount),
                    'product_id': str(item.product_id) if item.product_id else '',
                    'product_name': item.product.name if item.product else '',
                }
            )
        return serialized

    def _scope_values_from_purchase(self, purchase) -> dict:
        if not purchase:
            return {'farm_id': None, 'chicken_house_id': None, 'batch_code': ''}
        return {
            'farm_id': purchase.scope_farm_id,
            'chicken_house_id': purchase.scope_chicken_house_id,
            'batch_code': purchase.scope_batch_code or '',
        }

    def _scope_area_value_from_purchase(self, purchase) -> str:
        if not purchase:
            return PurchaseRequest.AreaScope.COMPANY
        if purchase.scope_area == PurchaseRequest.AreaScope.CHICKEN_HOUSE and purchase.scope_chicken_house_id:
            return f'{PurchaseRequest.AreaScope.CHICKEN_HOUSE}:{purchase.scope_chicken_house_id}'
        if purchase.scope_area == PurchaseRequest.AreaScope.FARM and purchase.scope_farm_id:
            return f'{PurchaseRequest.AreaScope.FARM}:{purchase.scope_farm_id}'
        return PurchaseRequest.AreaScope.COMPANY

    def _scope_area_value_from_scope(self, *, scope: dict, area_kind: str | None) -> str:
        house_id = scope.get('chicken_house_id')
        farm_id = scope.get('farm_id')
        if area_kind == PurchaseRequest.AreaScope.CHICKEN_HOUSE and house_id:
            return f'{PurchaseRequest.AreaScope.CHICKEN_HOUSE}:{house_id}'
        if area_kind == PurchaseRequest.AreaScope.FARM and farm_id:
            return f'{PurchaseRequest.AreaScope.FARM}:{farm_id}'
        if house_id:
            return f'{PurchaseRequest.AreaScope.CHICKEN_HOUSE}:{house_id}'
        if farm_id:
            return f'{PurchaseRequest.AreaScope.FARM}:{farm_id}'
        return PurchaseRequest.AreaScope.COMPANY

    def _normalize_scope_area_selection(
        self,
        *,
        raw_value: str,
        farm_id: int | None,
        chicken_house_id: int | None,
    ) -> dict:
        normalized_farm = farm_id
        normalized_house = chicken_house_id
        raw_value = (raw_value or '').strip()
        kind = PurchaseRequest.AreaScope.COMPANY
        error = None
        selection_value = raw_value
        if not raw_value:
            if normalized_house:
                kind = PurchaseRequest.AreaScope.CHICKEN_HOUSE
                selection_value = f'{kind}:{normalized_house}'
            elif normalized_farm:
                kind = PurchaseRequest.AreaScope.FARM
                selection_value = f'{kind}:{normalized_farm}'
            else:
                selection_value = PurchaseRequest.AreaScope.COMPANY
        elif raw_value == PurchaseRequest.AreaScope.COMPANY:
            kind = PurchaseRequest.AreaScope.COMPANY
            normalized_farm = None
            normalized_house = None
            selection_value = PurchaseRequest.AreaScope.COMPANY
        else:
            match = re.match(r'^(farm|chicken_house):(\d+)$', raw_value)
            if not match:
                error = "Selecciona un área válida."
                kind = PurchaseRequest.AreaScope.COMPANY
                normalized_farm = None
                normalized_house = None
                selection_value = PurchaseRequest.AreaScope.COMPANY
            else:
                target_kind = match.group(1)
                target_id = int(match.group(2))
                if target_kind == 'farm':
                    kind = PurchaseRequest.AreaScope.FARM
                    normalized_farm = target_id
                    normalized_house = None
                else:
                    kind = PurchaseRequest.AreaScope.CHICKEN_HOUSE
                    normalized_house = target_id
        if kind != PurchaseRequest.AreaScope.CHICKEN_HOUSE:
            normalized_house = None
        if kind == PurchaseRequest.AreaScope.COMPANY:
            normalized_farm = None
        if not selection_value:
            selection_value = PurchaseRequest.AreaScope.COMPANY
        return {
            'kind': kind,
            'value': selection_value,
            'farm_id': normalized_farm,
            'chicken_house_id': normalized_house,
            'error': error,
        }

    def _blank_item_row(self) -> dict[str, str]:
        return {
            'id': '',
            'description': '',
            'quantity': '',
            'estimated_amount': '',
            'product_id': '',
            'product_name': '',
        }

    def _enrich_item_rows(self, rows: list[dict[str, str]]) -> list[dict[str, str]]:
        if not rows:
            return rows
        product_index = {
            str(product['id']): product
            for product in self.purchase_form_options['products']
        }
        for row in rows:
            product = product_index.get(str(row.get('product_id') or '').strip())
            if product and not row.get('description'):
                row['description'] = product['name']
            row['product_name'] = product['name'] if product else ''
        return rows

    def _build_category_picker(self, selected_value):
        groups: dict[str, list[dict[str, str]]] = {}
        categories = self.purchase_form_options['categories']
        category_map = {category['id']: category for category in categories}
        grouped: dict[str, dict[str, object]] = {}

        def build_option(category: dict) -> dict[str, str]:
            return {
                'value': str(category['id']),
                'label': category['name'],
                'description': category.get('approval_summary') or '',
                'search_text': f"{category['name']} {category.get('approval_summary') or ''}".strip().lower(),
            }

        for category in categories:
            parent_id = category.get('parent_id')
            if parent_id:
                parent = category_map.get(parent_id)
                if parent:
                    group_key = f"parent:{parent_id}"
                    label = parent['name']
                else:
                    group_key = f"legacy:{parent_id}"
                    label = category.get('parent_label') or 'Generales'
                bucket = grouped.setdefault(
                    group_key,
                    {'label': label, 'children': [], 'parent_option': None},
                )
                bucket['children'].append(build_option(category))
                if parent and bucket.get('parent_option') is None:
                    bucket['parent_option'] = build_option(parent)
            else:
                group_key = f"parent:{category['id']}"
                bucket = grouped.setdefault(
                    group_key,
                    {'label': category['name'], 'children': [], 'parent_option': None},
                )
                bucket['parent_option'] = build_option(category)

        normalized_groups: dict[str, list[dict[str, str]]] = {}
        for _, bucket in sorted(grouped.items(), key=lambda item: str(item[1]['label']).lower()):
            label = str(bucket['label'])
            options: list[dict[str, str]] = []
            parent_option = bucket.get('parent_option')
            if parent_option:
                options.append(parent_option)
            children = sorted(bucket['children'], key=lambda option: option['label'].lower())
            options.extend(children)
            if label in normalized_groups:
                normalized_groups[label].extend(options)
            else:
                normalized_groups[label] = options
        return self._build_select_picker_config(
            name='expense_type',
            selected_value=selected_value,
            placeholder='Selecciona una categoría',
            groups=normalized_groups,
            search_enabled=True,
        )

    def _build_supplier_picker(self, selected_value):
        groups: dict[str, list[dict[str, str]]] = {}
        for supplier in self.purchase_form_options['suppliers']:
            label = (supplier['name'][:1].upper() if supplier['name'] else '#')
            option = {
                'value': str(supplier['id']),
                'label': supplier['name'],
                'description': '',
                'search_text': (supplier['name'] or '').lower(),
            }
            groups.setdefault(label, []).append(option)
        return self._build_select_picker_config(
            name='supplier',
            selected_value=selected_value,
            placeholder='Selecciona un tercero',
            groups=groups,
            search_enabled=True,
        )

    def _build_area_picker(self, *, selected_value: str | None) -> dict:
        groups: dict[str, list[dict[str, str]]] = {}
        groups['General'] = [
            {
                'value': PurchaseRequest.AreaScope.COMPANY,
                'label': 'Empresa',
                'description': 'Gasto corporativo',
                'search_text': 'empresa',
                'data_attrs': {'area-kind': PurchaseRequest.AreaScope.COMPANY},
            }
        ]
        farm_options: list[dict[str, str]] = []
        for farm in self.purchase_form_options['farms']:
            farm_options.append(
                {
                    'value': f"{PurchaseRequest.AreaScope.FARM}:{farm['id']}",
                    'label': farm['name'],
                    'description': 'Granja',
                    'search_text': farm['name'].lower(),
                    'data_attrs': {
                        'area-kind': PurchaseRequest.AreaScope.FARM,
                        'area-farm-id': str(farm['id']),
                    },
                }
            )
        if farm_options:
            groups['Granjas'] = farm_options
        house_options: list[dict[str, str]] = []
        for house in self.purchase_form_options['chicken_houses']:
            farm_name = house.get('farm_name') or ''
            label = house.get('label') or house['name']
            description_bits = [bit for bit in ('Galpón', farm_name) if bit]
            house_options.append(
                {
                    'value': f"{PurchaseRequest.AreaScope.CHICKEN_HOUSE}:{house['id']}",
                    'label': label,
                    'description': ' · '.join(description_bits),
                    'search_text': f"{label} {farm_name}".strip().lower(),
                    'data_attrs': {
                        'area-kind': PurchaseRequest.AreaScope.CHICKEN_HOUSE,
                        'area-farm-id': str(house.get('farm_id') or ''),
                        'area-house-id': str(house['id']),
                    },
                }
            )
        if house_options:
            groups['Galpones'] = house_options
        return self._build_select_picker_config(
            name='scope_area',
            selected_value=selected_value,
            placeholder='Selecciona un área',
            groups=groups,
            search_enabled=True,
            sort_groups=False,
        )

    def _build_select_picker_config(
        self,
        *,
        name: str,
        selected_value: int | str | None,
        placeholder: str,
        groups: dict[str, list[dict[str, str]]],
        search_enabled: bool,
        sort_groups: bool = True,
    ) -> dict:
        selected_value_str = str(selected_value) if selected_value else ''
        normalized_groups: list[dict[str, list[dict[str, str]]]] = []
        selected_label = placeholder
        selected_description = ''
        group_keys = list(groups.keys())
        if sort_groups:
            group_keys = sorted(group_keys, key=lambda item: item.lower())
        for label in group_keys:
            raw_options = sorted(groups[label], key=lambda option: option['label'].lower())
            normalized_options: list[dict[str, str]] = []
            for option in raw_options:
                normalized_option = {
                    'value': option['value'],
                    'label': option['label'],
                    'description': option.get('description') or '',
                    'search_text': option.get('search_text') or (option['label'] or '').lower(),
                    'data_attrs': option.get('data_attrs') or {},
                }
                normalized_options.append(normalized_option)
                if normalized_option['value'] == selected_value_str:
                    selected_label = normalized_option['label']
                    selected_description = normalized_option.get('description') or ''
            normalized_groups.append({'label': label, 'options': normalized_options})
        return {
            'name': name,
            'selected_value': selected_value_str,
            'selected_label': selected_label,
            'selected_description': selected_description,
            'groups': normalized_groups,
            'placeholder': placeholder,
            'search_enabled': search_enabled,
        }

    @cached_property
    def purchase_form_options(self) -> dict:
        categories = [
            {
                'id': category.id,
                'name': category.name,
                'approval_summary': category.approval_phase_summary,
                'support_type_id': category.default_support_document_type_id,
                'parent_label': category.parent_category.name if category.parent_category else '',
                'parent_id': category.parent_category_id,
            }
            for category in PurchasingExpenseType.objects.select_related('parent_category').order_by('name')
        ]
        suppliers = [
            {'id': supplier.id, 'name': supplier.name}
            for supplier in Supplier.objects.order_by('name')
        ]
        farms = [{'id': farm.id, 'name': farm.name} for farm in Farm.objects.order_by('name')]
        houses = [
            {
                'id': house.id,
                'name': house.name,
                'label': f'{house.farm.name} - {house.name}',
                'farm_id': house.farm_id,
                'farm_name': house.farm.name if house.farm else '',
            }
            for house in ChickenHouse.objects.select_related('farm').order_by('farm__name', 'name')
        ]
        bird_batches = [
            {
                'id': batch.id,
                'value': self._format_bird_batch_value(batch),
                'label': self._format_bird_batch_label(batch),
            }
            for batch in BirdBatch.objects.select_related('farm').order_by('-birth_date', 'farm__name')
        ]
        support_types = [
            {
                'id': support.id,
                'name': support.name,
                'kind': support.kind,
                'kind_label': support.get_kind_display(),
                'template': support.template,
            }
            for support in SupportDocumentType.objects.order_by('name')
        ]
        products = [
            {
                'id': product.id,
                'name': product.name,
                'unit': product.unit,
            }
            for product in Product.objects.order_by('name')
        ]
        return {
            'categories': categories,
            'suppliers': suppliers,
            'farms': farms,
            'chicken_houses': houses,
            'bird_batches': bird_batches,
            'support_types': support_types,
            'products': products,
        }

    def _format_decimal(self, value: Decimal | None) -> str:
        if value is None:
            return ''
        return format(value.normalize(), 'f')

    def _format_bird_batch_label(self, batch: BirdBatch) -> str:
        farm_name = batch.farm.name if batch.farm else 'Sin granja'
        return f'Lote #{batch.pk} · {farm_name}'

    def _format_bird_batch_value(self, batch: BirdBatch) -> str:
        # Persist the human-readable label to keep compatibility with existing scope batch codes.
        return self._format_bird_batch_label(batch)

    def _build_base_url(self, *, scope: str | None, extra: dict | None = None) -> str:
        base = reverse('administration:purchases')
        params = {}
        if scope:
            params['scope'] = scope
        if extra:
            params.update({k: str(v) for k, v in extra.items()})
        search_query = (self.request.GET.get('search') or self.request.POST.get('search') or '').strip()
        if search_query and 'search' not in params:
            params['search'] = search_query
        start_date_value = (self.request.GET.get('start_date') or self.request.POST.get('start_date') or '').strip()
        if start_date_value and 'start_date' not in params:
            params['start_date'] = start_date_value
        end_date_value = (self.request.GET.get('end_date') or self.request.POST.get('end_date') or '').strip()
        if end_date_value and 'end_date' not in params:
            params['end_date'] = end_date_value
        page_value = (self.request.GET.get('page') or self.request.POST.get('page') or '').strip()
        if page_value and 'page' not in params:
            params['page'] = page_value
        query = f"?{urlencode(params)}" if params else ""
        return f"{base}{query}"

    def _first_error_message(self, field_errors: dict[str, list[str]] | None) -> str:
        default_message = "No se pudo contabilizar la compra."
        if not field_errors:
            return default_message
        for messages_list in field_errors.values():
            if messages_list:
                return messages_list[0]
        return default_message

    def _build_support_template_catalog(self, *, purchase: PurchaseRequest | None) -> dict[str, dict[str, str]]:
        if not purchase:
            return {}

        supplier = purchase.supplier
        requester = purchase.requester
        catalog: dict[str, dict[str, str]] = {}

        def add(key: str, label: str, value: str | None) -> None:
            catalog[key] = {
                'label': label,
                'value': (value or '').strip(),
            }

        add('timeline_code', 'Código solicitud', purchase.timeline_code)
        add('request_name', 'Nombre de la solicitud', purchase.name)
        add('request_description', 'Descripción', purchase.description)
        add('category_name', 'Categoría', purchase.expense_type.name if purchase.expense_type else '')
        add('scope_label', 'Ámbito', purchase.scope_label)
        add('scope_area', 'Tipo de área', purchase.get_scope_area_display() if purchase.scope_area else '')
        add('scope_batch', 'Lote asociado', purchase.scope_batch_code)
        add('farm_name', 'Granja', purchase.scope_farm.name if purchase.scope_farm else '')
        add('chicken_house_name', 'Galpón', purchase.scope_chicken_house.name if purchase.scope_chicken_house else '')
        add('support_type_name', 'Tipo de soporte', purchase.support_document_type.name if purchase.support_document_type else '')
        add('status', 'Estado actual', purchase.get_status_display())

        add('eta', 'ETA', self._format_support_date(purchase.eta))
        add('order_number', 'Número de orden', purchase.order_number)
        add('order_date', 'Fecha orden', self._format_support_date(purchase.order_date))
        add('purchase_date', 'Fecha de compra', self._format_support_date(purchase.purchase_date))
        add('invoice_number', 'Número de factura', purchase.invoice_number)
        add('invoice_date', 'Fecha factura', self._format_support_date(purchase.invoice_date))
        add('invoice_total', 'Total factura', self._format_currency_value(purchase.currency, purchase.invoice_total))
        add('estimated_total', 'Total estimado', self._format_currency_value(purchase.currency, purchase.estimated_total))
        add('payment_amount', 'Monto pagado', self._format_currency_value(purchase.currency, purchase.payment_amount))

        add('currency', 'Moneda', purchase.currency)
        add('payment_condition', 'Condición de pago', purchase.get_payment_condition_display() if purchase.payment_condition else '')
        add('payment_method', 'Medio de pago', purchase.get_payment_method_display() if purchase.payment_method else '')
        add('payment_source', 'Origen del pago', purchase.get_payment_source_display() if purchase.payment_source else '')
        add('payment_account', 'Cuenta de pago', purchase.payment_account)
        add('payment_notes', 'Notas de pago', purchase.payment_notes)
        add('reception_notes', 'Notas de recepción', purchase.reception_notes)
        add('shipping_notes', 'Notas de envío', purchase.shipping_notes)
        add('delivery_condition', 'Condición de entrega', purchase.get_delivery_condition_display() if purchase.delivery_condition else '')
        add('shipping_eta', 'Fecha estimada de despacho', self._format_support_date(purchase.shipping_eta))

        add('requester_name', 'Solicitante', requester.get_full_name() if requester else '')
        requester_email = ''
        if requester:
            requester_email = getattr(requester, 'email', '') or getattr(getattr(requester, 'user', None), 'email', '')
        add('requester_email', 'Correo solicitante', requester_email)

        add('supplier_name', 'Proveedor', supplier.name if supplier else '')
        add('supplier_tax_id', 'Identificación proveedor', supplier.tax_id if supplier else '')
        add('supplier_contact_name', 'Contacto proveedor', supplier.contact_name if supplier else '')
        add('supplier_contact_email', 'Correo proveedor', supplier.contact_email if supplier else '')
        add('supplier_contact_phone', 'Teléfono proveedor', supplier.contact_phone if supplier else '')
        add('supplier_address', 'Dirección proveedor', supplier.address if supplier else '')
        add('supplier_city', 'Ciudad proveedor', supplier.city if supplier else '')
        add('supplier_account_holder_name', 'Titular de cuenta proveedor', supplier.account_holder_name if supplier else '')
        add('supplier_account_holder_id', 'ID titular proveedor', supplier.account_holder_id if supplier else '')
        add('supplier_account_type', 'Tipo de cuenta proveedor', dict(Supplier.ACCOUNT_TYPE_CHOICES).get(supplier.account_type, '') if supplier and supplier.account_type else '')
        add('supplier_account_number', 'Número de cuenta proveedor', supplier.account_number if supplier else '')
        add('supplier_bank_name', 'Banco proveedor', supplier.bank_name if supplier else '')

        add('created_at', 'Fecha de creación', self._format_datetime_value(purchase.created_at))
        add('updated_at', 'Última actualización', self._format_datetime_value(purchase.updated_at))
        add('approved_at', 'Aprobado en', self._format_datetime_value(purchase.approved_at))

        add('items_summary', 'Items solicitados', self._build_items_summary(purchase))

        return catalog

    def _format_support_date(self, value) -> str:
        if not value:
            return ''
        return value.strftime("%Y-%m-%d")

    def _format_datetime_value(self, value) -> str:
        if not value:
            return ''
        localized = timezone.localtime(value)
        return localized.strftime("%Y-%m-%d %H:%M")

    def _format_currency_value(self, currency: str | None, amount) -> str:
        if amount in (None, ''):
            return ''
        formatted = f"{amount:,.2f}"
        return f"{currency or ''} {formatted}".strip()

    def _build_items_summary(self, purchase: PurchaseRequest) -> str:
        if not hasattr(purchase, "items"):
            return ''
        lines: list[str] = []
        for item in purchase.items.all():
            qty = self._format_decimal(item.quantity)
            lines.append(f"- {item.description} · {qty}")
        return "\n".join(lines)

    def _parse_template_fields(self) -> dict[str, str]:
        values: dict[str, str] = {}
        field_pattern = re.compile(r"^template_fields\[(?P<name>[^\]]+)\]$")
        for key in self.request.POST.keys():
            match = field_pattern.match(key)
            if not match:
                continue
            values[match.group('name')] = (self.request.POST.get(key) or '').strip()
        return values

    def _merge_field_errors(
        self,
        target: dict[str, list[str]],
        source: dict[str, list[str]] | None,
    ) -> None:
        if not source:
            return
        for key, messages_list in source.items():
            target.setdefault(key, []).extend(messages_list)

    def _merge_item_errors(
        self,
        target: dict[int, dict[str, list[str]]],
        source: dict[int, dict[str, list[str]]] | None,
    ) -> None:
        if not source:
            return
        for index, row_errors in source.items():
            target.setdefault(index, {})
            for field, messages_list in row_errors.items():
                target[index].setdefault(field, []).extend(messages_list)


class ProductManagementView(StaffRequiredMixin, generic.TemplateView):
    template_name = 'administration/purchases/products.html'

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        return super().get(request, *args, **kwargs)

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        action = request.POST.get('form_action')
        if action == 'product':
            return self._submit_product_form()
        if action == 'delete':
            return self._delete_product()
        messages.error(request, 'Acción no soportada.')
        return redirect(self._base_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault('administration_active_submenu', 'products')
        search = (self.request.GET.get('search') or '').strip()
        panel_code = kwargs.get('panel') or self.request.GET.get('panel')
        product_id = _parse_int(self.request.GET.get('product'))
        qs = Product.objects.all()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(unit__icontains=search))
        paginator = Paginator(qs.order_by('name'), 20)
        products_page = paginator.get_page(self.request.GET.get('page') or 1)
        product_instance = None
        if product_id:
            product_instance = Product.objects.filter(pk=product_id).first()
        product_form = kwargs.get('product_form') or ProductForm(instance=product_instance)
        context.update(
            product_search=search,
            products_page=products_page,
            product_form=product_form,
            product_panel_open=kwargs.get('product_panel_force') or panel_code == 'product',
            product_instance=product_instance,
            delete_modal_open=self.request.GET.get('modal') == 'delete' and product_instance is not None,
        )
        return context

    def _submit_product_form(self) -> HttpResponse:
        product_id = _parse_int(self.request.POST.get('product_id'))
        instance = Product.objects.filter(pk=product_id).first() if product_id else None
        form = ProductForm(self.request.POST, instance=instance)
        if form.is_valid():
            form.save()
            verb = 'actualizado' if instance else 'registrado'
            messages.success(self.request, f'Producto {verb} correctamente.')
            return redirect(self._base_url(with_panel=False))
        messages.error(self.request, 'Revisa los errores del formulario.')
        return self.render_to_response(
            self.get_context_data(
                product_form=form,
                panel='product',
                product_panel_force=True,
                product_instance=instance,
            )
        )

    def _delete_product(self) -> HttpResponse:
        product_id = _parse_int(self.request.POST.get('product_id'))
        product = Product.objects.filter(pk=product_id).first()
        if not product:
            messages.error(self.request, 'Producto no encontrado.')
            return redirect(self._base_url())
        product.delete()
        messages.success(self.request, 'Producto eliminado.')
        return redirect(self._base_url())

    def _base_url(self, *, with_panel: bool = True) -> str:
        base = reverse('administration:purchases_products')
        params = {}
        search = self.request.GET.get('search')
        if search:
            params['search'] = search
        if with_panel and self.request.GET.get('panel'):
            params['panel'] = self.request.GET.get('panel')
        query = f"?{urlencode(params)}" if params else ""
        return f"{base}{query}"

class SupplierManagementView(StaffRequiredMixin, generic.TemplateView):
    template_name = 'administration/purchases/suppliers.html'

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        return super().get(request, *args, **kwargs)

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        action = request.POST.get('form_action')
        if action == 'supplier':
            return self._submit_supplier_form()
        if action == 'delete':
            return self._delete_supplier()
        messages.error(request, 'Acción no soportada.')
        return redirect(self._base_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault('administration_active_submenu', 'suppliers')
        search = self.request.GET.get('search', '').strip()
        panel_code = kwargs.get('panel') or self.request.GET.get('panel')
        supplier_id = _parse_int(self.request.GET.get('supplier'))
        qs = Supplier.objects.all()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(tax_id__icontains=search))
        paginator = Paginator(qs.order_by('name'), 20)
        page_number = self.request.GET.get('page') or 1
        suppliers_page = paginator.get_page(page_number)

        supplier_instance = None
        if supplier_id:
            supplier_instance = Supplier.objects.filter(pk=supplier_id).first()

        supplier_form = kwargs.get('supplier_form') or SupplierForm(instance=supplier_instance)
        context.update(
            supplier_search=search,
            suppliers_page=suppliers_page,
            supplier_form=supplier_form,
            supplier_panel_open=kwargs.get('supplier_panel_force') or panel_code == 'supplier',
            supplier_instance=supplier_instance,
            delete_modal_open=self.request.GET.get('modal') == 'delete' and supplier_instance is not None,
        )
        return context

    def _submit_supplier_form(self) -> HttpResponse:
        supplier_id = _parse_int(self.request.POST.get('supplier_id'))
        instance = Supplier.objects.filter(pk=supplier_id).first() if supplier_id else None
        form = SupplierForm(self.request.POST, instance=instance)
        if form.is_valid():
            supplier = form.save()
            verb = "actualizado" if instance else "registrado"
            messages.success(self.request, f"Tercero {verb} correctamente.")
            return redirect(self._base_url(with_panel=False))
        messages.error(self.request, "Revisa los errores del formulario.")
        return self.render_to_response(
            self.get_context_data(
                supplier_form=form,
                panel='supplier',
                supplier_panel_force=True,
                supplier_instance=instance,
            )
        )

    def _delete_supplier(self) -> HttpResponse:
        supplier_id = _parse_int(self.request.POST.get('supplier_id'))
        supplier = Supplier.objects.filter(pk=supplier_id).first()
        if not supplier:
            messages.error(self.request, "Tercero no encontrado.")
            return redirect(self._base_url())
        try:
            supplier.delete()
            messages.success(self.request, "Tercero eliminado.")
        except ProtectedError:
            messages.error(self.request, "No es posible eliminar este tercero porque tiene movimientos.")
        return redirect(self._base_url())

    def _base_url(self, *, with_panel: bool = True) -> str:
        base = reverse('administration:purchases_suppliers')
        params = {}
        search = self.request.GET.get('search')
        if search:
            params['search'] = search
        if with_panel and self.request.GET.get('panel'):
            params['panel'] = self.request.GET.get('panel')
        query = f"?{urlencode(params)}" if params else ""
        return f"{base}{query}"


class SupplierQuickCreateView(StaffRequiredMixin, generic.View):
    http_method_names = ['post']

    def post(self, request: HttpRequest, *args, **kwargs) -> JsonResponse:
        data = request.POST.copy()
        tax_id = (data.get('tax_id') or '').strip()
        name = (data.get('name') or '').strip()
        if not data.get('account_holder_id') and tax_id:
            data['account_holder_id'] = tax_id
        if not data.get('account_holder_name') and name:
            data['account_holder_name'] = name
        form = SupplierForm(data)
        if form.is_valid():
            supplier = form.save()
            display = supplier.name
            if supplier.tax_id:
                display = f"{supplier.name} · {supplier.tax_id}"
            return JsonResponse(
                {
                    'supplier': {
                        'id': str(supplier.pk),
                        'name': supplier.name,
                        'display': display,
                        'tax_id': supplier.tax_id,
                    }
                },
                status=201,
            )
        errors = {
            field: [str(error) for error in error_list]
            for field, error_list in form.errors.items()
        }
        return JsonResponse({'errors': errors}, status=400)


class PurchaseConfigurationView(StaffRequiredMixin, generic.TemplateView):
    template_name = 'administration/purchases/configuration.html'
    SECTION_TYPES = ('expense_types', 'support_documents')

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        section = self._current_section()
        if section == 'expense_types':
            return self._handle_expense_type_post()
        if section == 'support_documents':
            return self._handle_support_document_post()
        messages.error(request, "Sección no soportada.")
        return redirect(self._base_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault('administration_active_submenu', 'configuration')
        section = self._current_section()
        search = self.request.GET.get('search', '').strip()
        panel = kwargs.get('panel') or self.request.GET.get('panel')
        context.update(
            config_section=section,
            config_search=search,
            config_panel=panel,
        )
        if section == 'expense_types':
            context.update(
                self._expense_type_context(
                    search=search,
                    panel=panel,
                    expense_type_instance_override=kwargs.get('expense_type_instance_override'),
                    **kwargs,
                )
            )
        elif section == 'support_documents':
            context.update(
                self._support_document_context(
                    search=search,
                    panel=panel,
                    support_document_instance_override=kwargs.get('support_document_instance_override'),
                    **kwargs,
                )
            )
        delete_target = kwargs.get('delete_target')
        modal_requested = kwargs.get('delete_modal_force') or self.request.GET.get('modal') == 'delete'
        if not delete_target and modal_requested:
            if section == 'expense_types':
                delete_target = self._find_expense_type()
            elif section == 'support_documents':
                delete_target = self._find_support_document_type()
        context.update(
            config_delete_open=bool(delete_target and modal_requested),
            config_delete_target=delete_target,
            config_delete_section=section,
        )
        return context

    def _expense_type_context(self, *, search: str, panel: str | None, expense_type_instance_override=None, **kwargs):
        qs = PurchasingExpenseType.objects.prefetch_related('approval_rules', 'parent_category').order_by('name')
        if search:
            qs = qs.filter(Q(name__icontains=search))
        categories = list(qs)
        tree_rows = self._build_expense_type_rows(categories)
        total_categories = PurchasingExpenseType.objects.count()
        expense_type = expense_type_instance_override
        if not expense_type:
            expense_type = self._find_expense_type()
        form = kwargs.get('expense_type_form') or PurchasingExpenseTypeForm(instance=expense_type)
        workflow_formset = kwargs.get('workflow_formset') or self._build_workflow_formset(instance=expense_type)
        return {
            'expense_type_rows': tree_rows,
            'expense_type_total': total_categories,
            'expense_type_display_count': len(tree_rows),
            'expense_type_form': form,
            'expense_type_instance': expense_type,
            'expense_types_panel_open': panel == 'expense_type',
            'workflow_formset': workflow_formset,
        }

    def _support_document_context(
        self,
        *,
        search: str,
        panel: str | None,
        support_document_instance_override=None,
        **kwargs,
    ):
        qs = SupportDocumentType.objects.all()
        if search:
            qs = qs.filter(name__icontains=search)
        paginator = Paginator(qs.order_by('name'), 20)
        page_number = self.request.GET.get('page') or 1
        page_obj = paginator.get_page(page_number)
        support_document = support_document_instance_override or self._find_support_document_type()
        form = kwargs.get('support_document_form') or SupportDocumentTypeForm(instance=support_document)
        return {
            'support_document_types_page': page_obj,
            'support_document_form': form,
            'support_document_instance': support_document,
            'support_documents_panel_open': panel == 'support_document',
        }

    def _build_workflow_formset(self, *, instance: PurchasingExpenseType | None, data=None):
        target = instance or PurchasingExpenseType()
        kwargs = {'instance': target, 'prefix': 'workflow'}
        if data is not None:
            kwargs['data'] = data
        return ExpenseTypeWorkflowFormSet(**kwargs)

    def _build_expense_type_rows(self, categories):
        nodes: dict[int, dict] = {}
        for category in categories:
            nodes[category.id] = {'category': category, 'children': []}
        for node in nodes.values():
            parent_id = node['category'].parent_category_id
            if parent_id and parent_id in nodes:
                nodes[parent_id]['children'].append(node)
        for node in nodes.values():
            node['children'].sort(key=lambda item: item['category'].name.lower())
        roots = [
            node
            for node in nodes.values()
            if not node['category'].parent_category_id or node['category'].parent_category_id not in nodes
        ]
        roots.sort(key=lambda item: item['category'].name.lower())
        rows: list[dict] = []

        def traverse(node: dict, depth: int, parent_id: int | None) -> None:
            rows.append(
                {
                    'category': node['category'],
                    'depth': depth,
                    'parent_id': parent_id,
                    'has_children': bool(node['children']),
                }
            )
            for child in node['children']:
                traverse(child, depth + 1, node['category'].id)

        for root in roots:
            traverse(root, 0, None)
        return rows

    def _handle_expense_type_post(self) -> HttpResponse:
        action = self.request.POST.get('form_action')
        if action == 'expense_type':
            return self._save_expense_type()
        if action == 'delete':
            return self._delete_expense_type()
        messages.error(self.request, "Acción no soportada.")
        return redirect(self._base_url())

    def _handle_support_document_post(self) -> HttpResponse:
        action = self.request.POST.get('form_action')
        if action == 'support_document_type':
            return self._save_support_document_type()
        if action == 'delete':
            return self._delete_support_document_type()
        messages.error(self.request, "Acción no soportada.")
        return redirect(self._base_url(section='support_documents'))

    def _save_expense_type(self) -> HttpResponse:
        expense_type_id = _parse_int(self.request.POST.get('expense_type_id'))
        instance = PurchasingExpenseType.objects.filter(pk=expense_type_id).first() if expense_type_id else None
        form = PurchasingExpenseTypeForm(self.request.POST, instance=instance)
        workflow_formset = self._build_workflow_formset(
            instance=instance,
            data=self.request.POST,
        )
        workflow_formset_is_valid = workflow_formset.is_valid()
        if form.is_valid() and workflow_formset_is_valid:
            workflow_changed = workflow_formset.has_changed()
            with transaction.atomic():
                expense_type = form.save()
                workflow_formset.instance = expense_type
                workflow_formset.save()
                recalculated = 0
                if workflow_changed:
                    recalculated = ExpenseTypeWorkflowRefreshService(
                        expense_type=expense_type,
                        actor=self.request.user,
                    ).run()
            messages.success(self.request, "Categoría de gasto guardada.")
            if workflow_changed:
                if recalculated:
                    messages.info(
                        self.request,
                        f"Se recalcularon {recalculated} solicitudes en aprobación para esta categoría.",
                    )
                else:
                    messages.info(
                        self.request,
                        "No había solicitudes pendientes para recalcular.",
                    )
            return redirect(self._base_url(section='expense_types', with_panel=False))
        messages.error(self.request, "Revisa los errores del formulario.")
        return self.render_to_response(
            self.get_context_data(
                expense_type_form=form,
                panel='expense_type',
                expense_type_instance_override=instance,
                workflow_formset=workflow_formset,
            )
        )

    def _delete_expense_type(self) -> HttpResponse:
        expense_type_id = _parse_int(self.request.POST.get('expense_type_id'))
        expense_type = PurchasingExpenseType.objects.filter(pk=expense_type_id).first()
        if not expense_type:
            messages.error(self.request, "Categoría de gasto no encontrada.")
            return redirect(self._base_url(section='expense_types'))
        try:
            expense_type.delete()
            messages.success(self.request, "Categoría de gasto eliminada.")
        except ProtectedError:
            messages.error(self.request, "No es posible eliminar esta categoría de gasto porque tiene movimientos.")
        return redirect(self._base_url(section='expense_types'))

    def _save_support_document_type(self) -> HttpResponse:
        support_document_id = _parse_int(self.request.POST.get('support_document_type_id'))
        instance = SupportDocumentType.objects.filter(pk=support_document_id).first() if support_document_id else None
        form = SupportDocumentTypeForm(self.request.POST, instance=instance)
        if form.is_valid():
            support_type = form.save()
            verb = "actualizado" if instance else "registrado"
            messages.success(self.request, f"Tipo de soporte {verb} correctamente.")
            return redirect(self._base_url(section='support_documents', with_panel=False))
        messages.error(self.request, "Revisa los errores del formulario.")
        return self.render_to_response(
            self.get_context_data(
                section='support_documents',
                panel='support_document',
                support_document_form=form,
                support_document_instance_override=instance,
            )
        )

    def _delete_support_document_type(self) -> HttpResponse:
        support_document_id = _parse_int(self.request.POST.get('support_document_type_id'))
        support_document = SupportDocumentType.objects.filter(pk=support_document_id).first()
        if not support_document:
            messages.error(self.request, "Tipo de soporte no encontrado.")
            return redirect(self._base_url(section='support_documents'))
        try:
            support_document.delete()
            messages.success(self.request, "Tipo de soporte eliminado.")
        except ProtectedError:
            messages.error(self.request, "No puedes eliminar este tipo de soporte porque está en uso.")
        return redirect(self._base_url(section='support_documents'))

    def _current_section(self) -> str:
        requested = self.request.GET.get('section', 'expense_types')
        if requested not in self.SECTION_TYPES:
            requested = 'expense_types'
        return requested

    def _find_expense_type(self):
        expense_type_id = _parse_int(self.request.GET.get('expense_type'))
        if expense_type_id:
            return PurchasingExpenseType.objects.filter(pk=expense_type_id).first()
        return None

    def _find_support_document_type(self):
        support_document_id = _parse_int(self.request.GET.get('support_document'))
        if support_document_id:
            return SupportDocumentType.objects.filter(pk=support_document_id).first()
        return None

    def _base_url(self, *, section: str | None = None, with_panel: bool = True, extra_params: dict | None = None) -> str:
        base = reverse('administration:purchases_configuration')
        params: dict[str, str] = {}
        section_value = section or self._current_section()
        params['section'] = section_value
        search = self.request.GET.get('search') or self.request.POST.get('search')
        if search:
            params['search'] = search
        if with_panel and self.request.GET.get('panel'):
            params['panel'] = self.request.GET.get('panel')
        if extra_params:
            params.update({k: str(v) for k, v in extra_params.items()})
        query = f"?{urlencode(params)}" if params else ""
        return f"{base}{query}"


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


ITEM_KEY_PATTERN = re.compile(r"^items\[(\d+)]\[(\w+)]$")
RECEPTION_ITEM_PATTERN = re.compile(r"^receipts\[(\d+)]\[(\w+)]$")
