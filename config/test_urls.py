"""Test-specific URL configuration — excludes views that depend on django-tenants."""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from apps.companies import onboarding_urls
from apps.companies import urls as companies_urls


def health_check(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("health/", health_check, name="health-check"),
    path("zeus-admin/", include("apps.zeus_admin.urls")),
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("", include(onboarding_urls)),
    path("", include(companies_urls)),
    path("api/", include(companies_urls)),
]
