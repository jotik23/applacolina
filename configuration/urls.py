from django.urls import path
from django.views.generic import RedirectView

from administration.views import ProductManagementView, PurchaseConfigurationView
from configuration.views import (
    ConfigurationCollaboratorsView,
    ConfigurationCommandsView,
    ConfigurationPositionsView,
    ConfigurationTaskManagerView,
)
from production.views import (
    batch_allocation_delete_view,
    batch_management_view,
    batch_weight_registry_submit_view,
    bird_batch_delete_view,
    bird_batch_update_view,
    chicken_house_delete_view,
    chicken_house_update_view,
    farm_delete_view,
    farm_update_view,
    infrastructure_home_view,
    reference_tables_view,
    room_delete_view,
    room_update_view,
)

app_name = "configuration"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="configuration:tasks", permanent=False), name="index"),
    path("tareas/", ConfigurationTaskManagerView.as_view(), name="tasks"),
    path("colaboradores/", ConfigurationCollaboratorsView.as_view(), name="collaborators"),
    path("puestos/", ConfigurationPositionsView.as_view(), name="positions"),
    path("lotes/", batch_management_view, name="batches"),
    path(
        "lotes/<int:pk>/produccion/",
        RedirectView.as_view(pattern_name="home:batch-production-board", permanent=False),
        name="batch-production-board",
    ),
    path(
        "lotes/<int:pk>/produccion/pesos/",
        batch_weight_registry_submit_view,
        name="batch-production-weight-registry",
    ),
    path("lotes/<int:pk>/editar/", bird_batch_update_view, name="batch-update"),
    path("lotes/<int:pk>/eliminar/", bird_batch_delete_view, name="batch-delete"),
    path(
        "lotes/asignaciones/<int:pk>/eliminar/",
        batch_allocation_delete_view,
        name="batch-allocation-delete",
    ),
    path("infraestructura/", infrastructure_home_view, name="infrastructure"),
    path("tablas-referencia/", reference_tables_view, name="reference-tables"),
    path("productos/", ProductManagementView.as_view(), name="products"),
    path("comandos/", ConfigurationCommandsView.as_view(), name="commands"),
    path("infraestructura/granjas/<int:pk>/editar/", farm_update_view, name="farm-update"),
    path("infraestructura/granjas/<int:pk>/eliminar/", farm_delete_view, name="farm-delete"),
    path(
        "infraestructura/galpones/<int:pk>/editar/",
        chicken_house_update_view,
        name="chicken-house-update",
    ),
    path(
        "infraestructura/galpones/<int:pk>/eliminar/",
        chicken_house_delete_view,
        name="chicken-house-delete",
    ),
    path("infraestructura/salones/<int:pk>/editar/", room_update_view, name="room-update"),
    path("infraestructura/salones/<int:pk>/eliminar/", room_delete_view, name="room-delete"),
    path("gastos/", PurchaseConfigurationView.as_view(), name="expense-configuration"),
]
