from django.urls import path

from apps.companies.views import (
    onboarding_dna,
    onboarding_dna_reset,
    onboarding_index,
    onboarding_source_create,
    onboarding_status,
)

urlpatterns = [
    path("onboarding/", onboarding_index, name="onboarding-index"),
    path("onboarding/source/", onboarding_source_create, name="onboarding-source-create"),
    path("onboarding/status/<int:pk>/", onboarding_status, name="onboarding-status"),
    path("onboarding/dna/<int:pk>/", onboarding_dna, name="onboarding-dna"),
    path("onboarding/dna/reset/", onboarding_dna_reset, name="onboarding-dna-reset"),
]
