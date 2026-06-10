from django.urls import path

from apps.companies import views

urlpatterns = [
    path("company/", views.company_detail, name="company-detail"),
    path("company/dna/", views.dna_current, name="dna-current"),
    path("company/dna/history/", views.dna_history, name="dna-history"),
    path("company/dna/create/", views.dna_create, name="dna-create"),
    path("company/dna/generate/", views.dna_generate, name="dna-generate"),
    path("sources/", views.source_list_create, name="source-list-create"),
    path("sources/<int:pk>/", views.source_detail, name="source-detail"),
]
