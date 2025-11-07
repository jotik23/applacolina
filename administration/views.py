from __future__ import annotations

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
from django.utils.functional import cached_property
from django.views import generic

from applacolina.mixins import StaffRequiredMixin
from production.models import BirdBatch, ChickenHouse, Farm

from .forms import (
    ExpenseTypeWorkflowFormSet,
    PurchasingExpenseTypeForm,
    SupplierForm,
    SupportDocumentTypeForm,
)
from .models import (
    ExpenseTypeApprovalRule,
    PurchaseRequest,
    PurchasingExpenseType,
    Supplier,
    SupportDocumentType,
)
from .services.purchase_requests import (
    PurchaseItemPayload,
    PurchaseRequestPayload,
    PurchaseRequestSubmissionService,
    PurchaseRequestValidationError,
)
from .services.purchases import get_dashboard_state


class AdministrationHomeView(StaffRequiredMixin, generic.TemplateView):
    template_name = 'administration/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault('administration_active_submenu', 'purchases')
        scope_override = kwargs.get('scope_override')
        panel_override = kwargs.get('panel_override')
        purchase_pk_override = kwargs.get('purchase_pk_override')
        state = get_dashboard_state(
            scope_code=scope_override or self.request.GET.get('scope'),
            panel_code=panel_override or self.request.GET.get('panel'),
            purchase_pk=purchase_pk_override or _parse_int(self.request.GET.get('purchase')),
        )
        context.update(
            purchases_scope=state.scope,
            purchases_scopes=state.scopes,
            purchases_list=state.purchases,
            purchases_panel=state.panel,
            purchases_recent_activity=state.recent_activity,
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
        return context

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        panel = request.POST.get('panel')
        if panel == 'request':
            return self._handle_request_panel_post()
        messages.error(request, "El formulario enviado no está disponible todavía.")
        return redirect(self._build_base_url(scope=request.POST.get('scope')))

    def _handle_request_panel_post(self) -> HttpResponse:
        intent = self.request.POST.get('intent') or 'save_draft'
        scope_code = self.request.POST.get('scope') or PurchaseRequest.Status.DRAFT
        purchase_id = _parse_int(self.request.POST.get('purchase'))
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
        scope_values = {
            'farm_id': _parse_int(self.request.POST.get('scope_farm_id')),
            'chicken_house_id': _parse_int(self.request.POST.get('scope_chicken_house_id')),
            'batch_code': (self.request.POST.get('scope_batch_code') or '').strip(),
        }
        items_raw = self._extract_item_rows()
        overrides = {
            'summary': summary,
            'notes': notes,
            'expense_type_id': expense_type_id,
            'support_document_type_id': support_document_type_id,
            'supplier_id': supplier_id,
            'scope_values': scope_values,
            'items': items_raw,
            'scope_label': self._category_scope_label(expense_type_id),
        }
        field_errors: dict[str, list[str]] = {}
        item_errors: dict[int, dict[str, list[str]]] = {}

        item_payloads: list[PurchaseItemPayload] = []
        for index, row in enumerate(items_raw):
            row_errors: dict[str, list[str]] = {}
            description = (row.get('description') or '').strip()
            if not description:
                row_errors['description'] = ["La descripción es obligatoria."]

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
            )
        return payload, overrides, field_errors, item_errors

    def _parse_decimal(self, value: str | None, *, allow_empty: bool) -> Decimal | None:
        if value is None or value == '':
            return Decimal('0') if allow_empty else None
        try:
            return Decimal(value)
        except (InvalidOperation, TypeError):
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
                row.get('quantity'),
                row.get('estimated_amount'),
            )
            if not any(significant_fields):
                continue
            ordered_rows.append(row)
        return ordered_rows

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
        context = {
            'purchase_request_form': {
                'categories': self.purchase_form_options['categories'],
                'support_types': self.purchase_form_options['support_types'],
                'suppliers': self.purchase_form_options['suppliers'],
                'farms': self.purchase_form_options['farms'],
                'chicken_houses': self.purchase_form_options['chicken_houses'],
                'bird_batches': self.purchase_form_options['bird_batches'],
                'items': items,
                'scope_values': form_initial['scope'],
                'initial': form_initial['values'],
                'unit_label': form_initial['unit_label'],
                'read_only': form_initial['read_only'],
                'scope_code': form_initial['scope_code'],
                'scope_label': form_initial['scope_label'],
                'scope_requires_location': form_initial['scope_requires_location'],
            },
            'purchase_request_field_errors': field_errors,
            'purchase_request_item_errors': item_errors,
        }
        return context

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
            items = overrides.get('items') or []
            scope_code = self._category_scope_code(values.get('expense_type_id')) or (
                purchase.expense_type.scope if purchase and purchase.expense_type else ''
            )
            scope_label = overrides.get('scope_label') or self._category_scope_label(values.get('expense_type_id')) or (
                purchase.expense_type.get_scope_display() if purchase and purchase.expense_type else ''
            )
            unit_label = self._category_unit_label(values.get('expense_type_id')) or (
                purchase.expense_type.default_unit if purchase and purchase.expense_type else ''
            ) or 'Unidad'
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
            scope_code = purchase.expense_type.scope if purchase and purchase.expense_type else ''
            unit_label = (
                purchase.expense_type.default_unit
                if purchase and purchase.expense_type and purchase.expense_type.default_unit
                else 'Unidad'
            )
            scope_label = (
                purchase.expense_type.get_scope_display()
                if purchase and purchase.expense_type
                else ''
            )
        if not unit_label:
            unit_label = 'Unidad'
        location_scopes = {
            PurchasingExpenseType.Scope.FARM,
            PurchasingExpenseType.Scope.LOT,
        }
        return {
            'values': values,
            'scope': scope,
            'items': items,
            'read_only': read_only,
            'unit_label': unit_label,
            'scope_code': scope_code,
            'scope_label': scope_label,
            'scope_requires_location': scope_code in location_scopes,
        }

    def _serialize_items(self, purchase) -> list[dict[str, str]]:
        if not purchase:
            return []
        serialized: list[dict[str, str]] = []
        for item in purchase.items.all():
            serialized.append(
                {
                    'id': str(item.id),
                    'description': item.description,
                    'quantity': self._format_decimal(item.quantity),
                    'estimated_amount': self._format_decimal(item.estimated_amount),
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

    def _blank_item_row(self) -> dict[str, str]:
        return {
            'id': '',
            'description': '',
            'quantity': '',
            'estimated_amount': '',
        }

    @cached_property
    def purchase_form_options(self) -> dict:
        categories = [
            {
                'id': category.id,
                'name': category.name,
                'scope': category.scope,
                'scope_label': category.get_scope_display(),
                'approval_summary': category.approval_phase_summary,
                'unit': category.default_unit or '',
                'support_type_id': category.default_support_document_type_id,
            }
            for category in PurchasingExpenseType.objects.filter(is_active=True).order_by('name')
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
                'kind': support.get_kind_display(),
            }
            for support in SupportDocumentType.objects.order_by('name')
        ]
        return {
            'categories': categories,
            'suppliers': suppliers,
            'farms': farms,
            'chicken_houses': houses,
            'bird_batches': bird_batches,
            'support_types': support_types,
        }

    def _category_unit_label(self, category_id):
        if not category_id:
            return ''
        for category in self.purchase_form_options['categories']:
            if category['id'] == category_id:
                return category.get('unit') or ''
        return ''

    def _category_scope_label(self, category_id):
        if not category_id:
            return ''
        for category in self.purchase_form_options['categories']:
            if category['id'] == category_id:
                return category.get('scope_label') or ''
        return ''

    def _category_scope_code(self, category_id):
        if not category_id:
            return ''
        for category in self.purchase_form_options['categories']:
            if category['id'] == category_id:
                return category.get('scope') or ''
        return ''

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

    def _build_base_url(self, *, scope: str | None) -> str:
        base = reverse('administration:purchases')
        params = {}
        if scope:
            params['scope'] = scope
        query = f"?{urlencode(params)}" if params else ""
        return f"{base}{query}"

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
        qs = PurchasingExpenseType.objects.prefetch_related('approval_rules')
        if search:
            qs = qs.filter(Q(name__icontains=search))
        paginator = Paginator(qs.order_by('name'), 20)
        page_number = self.request.GET.get('page') or 1
        page_obj = paginator.get_page(page_number)
        expense_type = expense_type_instance_override
        if not expense_type:
            expense_type = self._find_expense_type()
        form = kwargs.get('expense_type_form') or PurchasingExpenseTypeForm(instance=expense_type)
        workflow_formset = kwargs.get('workflow_formset') or self._build_workflow_formset(instance=expense_type)
        return {
            'expense_types_page': page_obj,
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

    def _handle_expense_type_post(self) -> HttpResponse:
        action = self.request.POST.get('form_action')
        if action == 'expense_type':
            return self._save_expense_type()
        if action in {'activate', 'deactivate'}:
            return self._toggle_expense_type(is_active=(action == 'activate'))
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
        if form.is_valid() and workflow_formset.is_valid():
            with transaction.atomic():
                expense_type = form.save()
                workflow_formset.instance = expense_type
                workflow_formset.save()
            messages.success(self.request, "Categoría de gasto guardada.")
            return redirect(self._base_url(section='expense_types'))
        messages.error(self.request, "Revisa los errores del formulario.")
        return self.render_to_response(
            self.get_context_data(
                expense_type_form=form,
                panel='expense_type',
                expense_type_instance_override=instance,
                workflow_formset=workflow_formset,
            )
        )

    def _toggle_expense_type(self, *, is_active: bool) -> HttpResponse:
        expense_type_id = _parse_int(self.request.POST.get('expense_type_id'))
        expense_type = PurchasingExpenseType.objects.filter(pk=expense_type_id).first()
        if not expense_type:
            messages.error(self.request, "Categoría de gasto no encontrada.")
            return redirect(self._base_url(section='expense_types'))
        if expense_type.is_active == is_active:
            messages.info(self.request, "El estado ya coincidía.")
        else:
            expense_type.is_active = is_active
            expense_type.save(update_fields=['is_active'])
            messages.success(self.request, "Estado actualizado.")
        return redirect(self._base_url(section='expense_types', with_panel=False))

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
            return redirect(self._base_url(section='support_documents'))
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
