from django.urls import path

from apps.companies import views

urlpatterns = [
    path("company/", views.company_detail, name="company-detail"),
    path("company/dna/", views.dna_current, name="dna-current"),
    path("company/dna/history/", views.dna_history, name="dna-history"),
    path("company/dna/create/", views.dna_create, name="dna-create"),
    path("company/dna/generate/", views.dna_generate, name="dna-generate"),
    path("company/dna/<int:pk>/feedback/", views.dna_feedback, name="dna-feedback"),
    path("company/dna/review/", views.dna_review, name="dna-review"),
    path("company/dna/download/", views.dna_download_pdf, name="dna-download-pdf"),
    path(
        "company/dna/<int:pk>/section/<str:section_key>/approve/",
        views.dna_section_approve,
        name="dna-section-approve",
    ),
    path(
        "company/dna/<int:pk>/section/<str:section_key>/edit/",
        views.dna_section_edit,
        name="dna-section-edit",
    ),
    path("sources/", views.source_list_create, name="source-list-create"),
    path("sources/<int:pk>/", views.source_detail, name="source-detail"),
    path("pipeline/", views.pipeline_run_create, name="pipeline-run-create"),
    path("pipeline/<int:pk>/", views.pipeline_run_detail, name="pipeline-run-detail"),
]
