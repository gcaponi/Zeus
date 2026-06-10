from django.contrib import admin
from django.urls import path

from apps.core.views import health_check

urlpatterns = [
    path("health/", health_check, name="health-check"),
    path("admin/", admin.site.urls),
]