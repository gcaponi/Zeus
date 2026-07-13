from django.contrib import admin
from django.urls import include, path

from apps.companies import onboarding_urls
from apps.companies import urls as companies_urls
from apps.core.metrics import metrics_view
from apps.core.views import (
    ZEUSSignupView,
    app_shell_preview,
    health_check,
    public_login,
    public_logout,
    public_onboarding_redirect,
    tenant_dashboard,
    tenant_landing,
)

urlpatterns = [
    path("__shell_preview/", app_shell_preview, name="app-shell-preview"),
    path("health/", health_check, name="health-check"),
    path("metrics/", metrics_view, name="metrics"),
    path("zeus-admin/", include("apps.zeus_admin.urls")),
    path("admin/", admin.site.urls),
    path("", include(onboarding_urls)),
    path("accounts/signup/", ZEUSSignupView.as_view(), name="account_signup"),
    path("accounts/login/", public_login, name="account_login"),
    path("accounts/logout/", public_logout, name="account_logout"),
    path("accounts/", include("allauth.urls")),
    path("dashboard/", tenant_dashboard, name="tenant-dashboard"),
    path("onboarding/", public_onboarding_redirect, name="public-onboarding-redirect"),
    path("api/", include(companies_urls)),
    path("", include(companies_urls)),
    path("", tenant_landing, name="tenant-landing"),
]
