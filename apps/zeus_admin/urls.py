from django.urls import path

from apps.zeus_admin import views

urlpatterns = [
    path("", views.dashboard, name="zeus-admin-dashboard"),
]
