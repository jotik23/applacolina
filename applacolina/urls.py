"""
URL configuration for applacolina project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView, TemplateView

from applacolina.views import digital_asset_links_view

admin.site.site_header = "Administracion de La Colina"
admin.site.site_title = "Administracion de La Colina"
admin.site.index_title = "Panel de administracion"

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', RedirectView.as_view(pattern_name='task_manager:index', permanent=False)),
    path('.well-known/assetlinks.json', digital_asset_links_view, name='asset-links'),
    path(
        'service-worker.js',
        TemplateView.as_view(
            template_name='task_manager/pwa/service-worker.js',
            content_type='application/javascript',
        ),
        name='service-worker',
    ),
    path('portal/', include('personal.portal_urls', namespace='portal')),
    path('calendario/', include('personal.urls', namespace='personal')),
    path('administracion/', include('administration.urls', namespace='administration')),
    path('task-manager/', include('task_manager.urls', namespace='task_manager')),
    re_path(r'^producci[oó]n-avicola/', include('production.urls', namespace='production')),
    path('api/', include('personal.api_urls', namespace='personal-api')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
