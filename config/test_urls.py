"""Test-specific URL configuration — excludes views that depend on django-tenants."""

from django.contrib import admin
from django.urls import include, path

from apps.companies import urls as companies_urls

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(companies_urls)),
]
