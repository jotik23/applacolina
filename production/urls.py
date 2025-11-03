from django.urls import path

from .views import (
    chicken_house_delete_view,
    chicken_house_update_view,
    farm_delete_view,
    farm_update_view,
    infrastructure_home_view,
    production_home_view,
    room_delete_view,
    room_update_view,
)

app_name = "production"

urlpatterns = [
    path("", production_home_view, name="index"),
    path("infraestructura/", infrastructure_home_view, name="infrastructure"),
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
