from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views import generic

from applacolina.mixins import StaffRequiredMixin

from .forms import (
    ExpenseTypeWorkflowFormSet,
    PurchasingExpenseTypeForm,
    SupplierForm,
)
from .models import (
    ExpenseTypeApprovalRule,
    PurchasingExpenseType,
    Supplier,
)
from .services.purchases import get_dashboard_state


class AdministrationHomeView(StaffRequiredMixin, generic.TemplateView):
    template_name = 'administration/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault('administration_active_submenu', 'purchases')
        state = get_dashboard_state(
            scope_code=self.request.GET.get('scope'),
            panel_code=self.request.GET.get('panel'),
            purchase_pk=_parse_int(self.request.GET.get('purchase')),
        )
        context.update(
            purchases_scope=state.scope,
            purchases_scopes=state.scopes,
            purchases_list=state.purchases,
            purchases_panel=state.panel,
            purchases_recent_activity=state.recent_activity,
        )
        return context


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


class PurchaseConfigurationView(StaffRequiredMixin, generic.TemplateView):
    template_name = 'administration/purchases/configuration.html'
    SECTION_TYPES = ('expense_types', 'future')

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        section = self.request.GET.get('section', 'expense_types')
        if section == 'expense_types':
            return self._handle_expense_type_post()
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
        delete_target = kwargs.get('delete_target')
        modal_requested = kwargs.get('delete_modal_force') or self.request.GET.get('modal') == 'delete'
        if not delete_target and modal_requested:
            if section == 'expense_types':
                delete_target = self._find_expense_type()
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
