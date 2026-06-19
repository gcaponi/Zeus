from django.urls import path

from apps.zeus_admin import views

urlpatterns = [
    path("", views.dashboard, name="zeus-admin-dashboard"),
    path("clients/", views.clients, name="zeus-admin-clients"),
    path("clients/<int:client_id>/", views.client_detail, name="zeus-admin-client-detail"),
    path(
        "clients/<int:client_id>/company-files/<int:file_id>/",
        views.open_company_file,
        name="zeus-admin-company-file-open",
    ),
    path(
        "clients/<int:client_id>/company-files/<int:file_id>/delete/",
        views.delete_company_file,
        name="zeus-admin-company-file-delete",
    ),
    path(
        "clients/<int:client_id>/company-dna/<int:dna_id>/",
        views.open_company_dna,
        name="zeus-admin-company-dna-open",
    ),
    path(
        "clients/<int:client_id>/company-dna/<int:dna_id>/delete/",
        views.delete_company_dna,
        name="zeus-admin-company-dna-delete",
    ),
    path(
        "clients/<int:client_id>/product-files/<int:file_id>/",
        views.open_product_file,
        name="zeus-admin-product-file-open",
    ),
    path(
        "clients/<int:client_id>/product-files/<int:file_id>/delete/",
        views.delete_product_file,
        name="zeus-admin-product-file-delete",
    ),
    path(
        "clients/<int:client_id>/product-dna/<int:dna_id>/",
        views.open_product_dna,
        name="zeus-admin-product-dna-open",
    ),
    path(
        "clients/<int:client_id>/product-dna/<int:dna_id>/delete/",
        views.delete_product_dna,
        name="zeus-admin-product-dna-delete",
    ),
]
