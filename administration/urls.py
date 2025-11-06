from django.urls import path
from django.views.generic import RedirectView

from .views import AdministrationHomeView

app_name = 'administration'

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='administration:purchases', permanent=False), name='index'),
    path('compras/', AdministrationHomeView.as_view(), name='purchases'),
]

