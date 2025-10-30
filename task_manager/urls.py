from django.urls import path

from .views import task_manager_home_view

app_name = "task_manager"

urlpatterns = [
    path("", task_manager_home_view, name="index"),
]

