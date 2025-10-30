from django.urls import path

from .views import production_home_view

app_name = "production"

urlpatterns = [
    path("", production_home_view, name="index"),
]
