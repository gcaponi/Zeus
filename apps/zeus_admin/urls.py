from django.urls import path

from apps.zeus_admin import views

urlpatterns = [
    path("", views.dashboard, name="zeus-admin-dashboard"),
    path("clients/", views.clients, name="zeus-admin-clients"),
    path("clients/<int:client_id>/", views.client_detail, name="zeus-admin-client-detail"),
]
