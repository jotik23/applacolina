from django.urls import path

from .views import (
    mini_app_dev_logout_view,
    mini_app_logout_view,
    mini_app_task_complete_view,
    mini_app_task_evidence_upload_view,
    mini_app_task_reset_view,
    telegram_mini_app_dev_demo_view,
    telegram_mini_app_dev_view,
    telegram_mini_app_demo_view,
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
    path("telegram/mini-app/logout/", mini_app_logout_view, name="telegram-mini-app-logout"),
    path("telegram/mini-app/demo/", telegram_mini_app_demo_view, name="telegram-mini-app-demo"),
    path("telegram/mini-app/dev/", telegram_mini_app_dev_view, name="telegram-mini-app-dev"),
    path("telegram/mini-app/dev/logout/", mini_app_dev_logout_view, name="telegram-mini-app-dev-logout"),
    path("telegram/mini-app/dev/demo/", telegram_mini_app_dev_demo_view, name="telegram-mini-app-dev-demo"),
    path(
        "telegram/mini-app/tasks/<int:pk>/complete/",
        mini_app_task_complete_view,
        name="mini-app-task-complete",
    ),
    path(
        "telegram/mini-app/tasks/<int:pk>/evidence/",
        mini_app_task_evidence_upload_view,
        name="mini-app-task-evidence",
    ),
    path(
        "telegram/mini-app/tasks/<int:pk>/reset/",
        mini_app_task_reset_view,
        name="mini-app-task-reset",
    ),
    path("definitions/create/", task_definition_create_view, name="definition-create"),
    path("definitions/<int:pk>/", task_definition_detail_view, name="definition-detail"),
    path("definitions/<int:pk>/update/", task_definition_update_view, name="definition-update"),
    path("definitions/<int:pk>/delete/", task_definition_delete_view, name="definition-delete"),
    path("definitions/rows/", task_definition_list_view, name="definition-rows"),
    path("definitions/reorder/", task_definition_reorder_view, name="definition-reorder"),
]
