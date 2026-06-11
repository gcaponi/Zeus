from django.contrib import admin
from django.urls import include, path

from apps.companies import onboarding_urls
from apps.companies import urls as companies_urls
from apps.core.views import ZEUSSignupView, health_check, tenant_dashboard, tenant_landing

urlpatterns = [
    path("health/", health_check, name="health-check"),
    path("admin/", admin.site.urls),
    path("", include(onboarding_urls)),
    path("accounts/signup/", ZEUSSignupView.as_view(), name="account_signup"),
    path("accounts/", include("allauth.urls")),
    path("dashboard/", tenant_dashboard, name="tenant-dashboard"),
    path("api/", include(companies_urls)),
    path("", tenant_landing, name="tenant-landing"),
]
