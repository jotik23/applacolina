from django.urls import path
from django.views.generic import RedirectView

from .views import (
    mini_app_logout_view,
    mini_app_push_subscription_view,
    mini_app_push_test_view,
    mini_app_push_subscription_view,
    mini_app_production_record_view,
    mini_app_purchase_request_view,
    mini_app_purchase_supplier_search_view,
    mini_app_purchase_supplier_create_view,
    mini_app_purchase_order_view,
    mini_app_purchase_finalize_view,
    mini_app_purchase_approval_view,
    mini_app_purchase_request_modify_view,
    mini_app_rest_suggestion_view,
    mini_app_task_complete_view,
    mini_app_task_evidence_upload_view,
    mini_app_task_reset_view,
    mini_app_transport_authorize_view,
    mini_app_transport_confirmation_view,
    mini_app_transport_progress_view,
    mini_app_transport_verification_view,
    mini_app_weight_registry_view,
    mini_app_night_mortality_view,
    telegram_mini_app_demo_view,
    telegram_mini_app_view,
    task_definition_create_view,
    task_definition_delete_view,
    task_definition_detail_view,
    task_definition_duplicate_view,
    task_definition_list_view,
    task_definition_reorder_view,
    task_definition_update_view,
    task_manager_home_view,
)

app_name = "task_manager"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="configuration:tasks", permanent=False), name="index"),
    path("telegram/mini-app/", telegram_mini_app_view, name="telegram-mini-app"),
    path("telegram/mini-app/logout/", mini_app_logout_view, name="telegram-mini-app-logout"),
    path("telegram/mini-app/demo/", telegram_mini_app_demo_view, name="telegram-mini-app-demo"),
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
    path(
        "telegram/mini-app/rest-suggestions/",
        mini_app_rest_suggestion_view,
        name="mini-app-rest-suggestions",
    ),
    path(
        "telegram/mini-app/production-records/",
        mini_app_production_record_view,
        name="mini-app-production-records",
    ),
    path(
        "telegram/mini-app/night-mortality/",
        mini_app_night_mortality_view,
        name="mini-app-night-mortality",
    ),
    path(
        "telegram/mini-app/purchases/<int:pk>/request-modification/",
        mini_app_purchase_request_modify_view,
        name="mini-app-purchase-request-modify",
    ),
    path(
        "telegram/mini-app/purchases/requests/",
        mini_app_purchase_request_view,
        name="mini-app-purchase-request",
    ),
    path(
        "telegram/mini-app/purchases/suppliers/",
        mini_app_purchase_supplier_search_view,
        name="mini-app-purchase-suppliers",
    ),
    path(
        "telegram/mini-app/purchases/suppliers/create/",
        mini_app_purchase_supplier_create_view,
        name="mini-app-purchase-suppliers-create",
    ),
    path(
        "telegram/mini-app/purchases/<int:pk>/order/",
        mini_app_purchase_order_view,
        name="mini-app-purchase-order",
    ),
    path(
        "telegram/mini-app/purchases/<int:pk>/finalize/",
        mini_app_purchase_finalize_view,
        name="mini-app-purchase-finalize",
    ),
    path(
        "telegram/mini-app/purchases/<int:pk>/approval/",
        mini_app_purchase_approval_view,
        name="mini-app-purchase-approval",
    ),
    path(
        "telegram/mini-app/weight-registry/",
        mini_app_weight_registry_view,
        name="mini-app-weight-registry",
    ),
    path(
        "telegram/mini-app/transport/authorize/",
        mini_app_transport_authorize_view,
        name="mini-app-transport-authorize",
    ),
    path(
        "telegram/mini-app/transport/progress/",
        mini_app_transport_progress_view,
        name="mini-app-transport-progress",
    ),
    path(
        "telegram/mini-app/transport/verification/",
        mini_app_transport_verification_view,
        name="mini-app-transport-verification",
    ),
    path(
        "telegram/mini-app/transport/confirmation/",
        mini_app_transport_confirmation_view,
        name="mini-app-transport-confirmation",
    ),
    path(
        "api/pwa/subscriptions/",
        mini_app_push_subscription_view,
        name="mini-app-pwa-subscriptions",
    ),
    path(
        "tools/push-test/",
        mini_app_push_test_view,
        name="mini-app-push-test",
    ),
    path("definitions/create/", task_definition_create_view, name="definition-create"),
    path("definitions/<int:pk>/", task_definition_detail_view, name="definition-detail"),
    path("definitions/<int:pk>/update/", task_definition_update_view, name="definition-update"),
    path("definitions/<int:pk>/duplicate/", task_definition_duplicate_view, name="definition-duplicate"),
    path("definitions/<int:pk>/delete/", task_definition_delete_view, name="definition-delete"),
    path("definitions/rows/", task_definition_list_view, name="definition-rows"),
    path("definitions/reorder/", task_definition_reorder_view, name="definition-reorder"),
]
