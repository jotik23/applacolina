from django.urls import path

from .views import (
    batch_production_board_view,
    batch_allocation_delete_view,
    batch_management_view,
    bird_batch_delete_view,
    bird_batch_update_view,
    chicken_house_delete_view,
    chicken_house_update_view,
    daily_indicators_view,
    farm_delete_view,
    farm_update_view,
    infrastructure_home_view,
    reference_tables_view,
    room_delete_view,
    room_update_view,
)

app_name = "production"

urlpatterns = [
    path("", daily_indicators_view, name="index"),
    path("indicadores-dia/", daily_indicators_view, name="daily-indicators"),
    path("lotes/", batch_management_view, name="batches"),
    path("lotes/<int:pk>/produccion/", batch_production_board_view, name="batch-production-board"),
    path("lotes/<int:pk>/editar/", bird_batch_update_view, name="batch-update"),
    path("lotes/<int:pk>/eliminar/", bird_batch_delete_view, name="batch-delete"),
    path(
        "lotes/asignaciones/<int:pk>/eliminar/",
        batch_allocation_delete_view,
        name="batch-allocation-delete",
    ),
    path("infraestructura/", infrastructure_home_view, name="infrastructure"),
    path("tablas-referencia/", reference_tables_view, name="reference-tables"),
    path("infraestructura/granjas/<int:pk>/editar/", farm_update_view, name="farm-update"),
    path("infraestructura/granjas/<int:pk>/eliminar/", farm_delete_view, name="farm-delete"),
    path("infraestructura/galpones/<int:pk>/editar/", chicken_house_update_view, name="chicken-house-update"),
    path(
        "infraestructura/galpones/<int:pk>/eliminar/",
        chicken_house_delete_view,
        name="chicken-house-delete",
    ),
    path("infraestructura/salones/<int:pk>/editar/", room_update_view, name="room-update"),
    path("infraestructura/salones/<int:pk>/eliminar/", room_delete_view, name="room-delete"),
]
