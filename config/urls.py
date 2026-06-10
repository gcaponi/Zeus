from django.contrib import admin
from django.urls import include, path

from apps.core.views import ZEUSSignupView, health_check, tenant_landing

urlpatterns = [
    path("health/", health_check, name="health-check"),
    path("admin/", admin.site.urls),
    path("accounts/signup/", ZEUSSignupView.as_view(), name="account_signup"),
    path("accounts/", include("allauth.urls")),
    path("", tenant_landing, name="tenant-landing"),
]
