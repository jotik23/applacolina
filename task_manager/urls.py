from django.urls import path

from .views import (
    telegram_mini_app_view,
    task_definition_create_view,
    task_definition_delete_view,
    task_definition_detail_view,
    task_definition_list_view,
    task_definition_reorder_view,
    task_definition_update_view,
    task_manager_home_view,
)

app_name = "task_manager"

urlpatterns = [
    path("", task_manager_home_view, name="index"),
    path("telegram/mini-app/", telegram_mini_app_view, name="telegram-mini-app"),
    path("definitions/create/", task_definition_create_view, name="definition-create"),
    path("definitions/<int:pk>/", task_definition_detail_view, name="definition-detail"),
    path("definitions/<int:pk>/update/", task_definition_update_view, name="definition-update"),
    path("definitions/<int:pk>/delete/", task_definition_delete_view, name="definition-delete"),
    path("definitions/rows/", task_definition_list_view, name="definition-rows"),
    path("definitions/reorder/", task_definition_reorder_view, name="definition-reorder"),
]
