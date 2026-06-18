from django.urls import path

from apps.companies import views

urlpatterns = [
    path("company/", views.company_detail, name="company-detail"),
    path("company/dna/", views.dna_current, name="dna-current"),
    path("company/dna/history/", views.dna_history, name="dna-history"),
    path("company/dna/create/", views.dna_create, name="dna-create"),
    path("company/dna/generate/", views.dna_generate, name="dna-generate"),
    path("company/dna/questions/", views.dna_questions, name="dna-questions"),
    path("company/dna/<int:pk>/feedback/", views.dna_feedback, name="dna-feedback"),
    path("company/dna/review/", views.dna_review, name="dna-review"),
    path("company/dna/generating/", views.dna_generating, name="dna-generating"),
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
    path("products/", views.product_list_create, name="product-list-create"),
    path("products/<int:pk>/", views.product_detail, name="product-detail"),
    path("products/<int:pk>/upload/", views.product_file_upload, name="product-file-upload"),
    path("products/<int:pk>/generate/", views.product_dna_generate, name="product-dna-generate"),
    path("products/<int:pk>/questions/", views.product_questions, name="product-questions"),
    path("products/<int:pk>/review/", views.product_review, name="product-review"),
    path(
        "products/<int:pk>/section/<str:section_key>/approve/",
        views.product_section_approve,
        name="product-section-approve",
    ),
    path(
        "products/<int:pk>/section/<str:section_key>/edit/",
        views.product_section_edit,
        name="product-section-edit",
    ),
    path("sources/", views.source_list_create, name="source-list-create"),
    path("sources/<int:pk>/", views.source_detail, name="source-detail"),
    path("pipeline/", views.pipeline_run_create, name="pipeline-run-create"),
    path("pipeline/<int:pk>/", views.pipeline_run_detail, name="pipeline-run-detail"),
]
