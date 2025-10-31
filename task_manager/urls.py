from django.urls import path

from .views import (
    task_definition_create_view,
    task_definition_delete_view,
    task_definition_detail_view,
    task_definition_update_view,
    task_manager_home_view,
)

app_name = "task_manager"

urlpatterns = [
    path("", task_manager_home_view, name="index"),
    path("definitions/create/", task_definition_create_view, name="definition-create"),
    path("definitions/<int:pk>/", task_definition_detail_view, name="definition-detail"),
    path("definitions/<int:pk>/update/", task_definition_update_view, name="definition-update"),
    path("definitions/<int:pk>/delete/", task_definition_delete_view, name="definition-delete"),
]
