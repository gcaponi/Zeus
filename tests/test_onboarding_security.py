"""Regressione sicurezza onboarding file (Codex Security finding 8).

La route onboarding-file-delete prima non aveva ne' login_required ne' un
guard sul metodo: una GET anonima con un id enumerabile cancellava documenti
del tenant. Deve rispondere: anonimo → redirect login, GET autenticato → 405,
id di un altro tenant → 404.
"""
import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import Http404
from django.test import RequestFactory
from django.urls import reverse

from apps.companies import views
from apps.companies.models import Company, CompanyFile

pytestmark = pytest.mark.django_db


def _company(schema="test-tenant", name="Sec Co"):
    return Company.objects.create(schema_name=schema, name=name)


def _request(rf_with_tenant, method, pk):
    return rf_with_tenant(method, reverse("onboarding-file-delete", args=[pk]))


class TestOnboardingFileDeleteSecurity:
    def test_anonymous_is_redirected_to_login(self, rf_with_tenant):
        company = _company()
        company_file = CompanyFile.objects.create(
            company=company, original_name="listino.txt", content_text="prezzi",
        )
        request = _request(rf_with_tenant, "post", company_file.pk)
        request.user = AnonymousUser()

        response = views.onboarding_file_delete(request, pk=company_file.pk)

        assert response.status_code == 302
        assert "/accounts/login/" in response.url
        assert CompanyFile.objects.filter(pk=company_file.pk).exists()

    def test_get_is_not_allowed(self, rf_with_tenant):
        company = _company()
        company_file = CompanyFile.objects.create(
            company=company, original_name="listino.txt", content_text="prezzi",
        )
        request = _request(rf_with_tenant, "get", company_file.pk)

        response = views.onboarding_file_delete(request, pk=company_file.pk)

        assert response.status_code == 405
        assert CompanyFile.objects.filter(pk=company_file.pk).exists()

    def test_cross_tenant_file_id_returns_404(self, rf_with_tenant):
        other = _company(schema="altro-tenant", name="Other Co")
        other_file = CompanyFile.objects.create(
            company=other, original_name="altrui.txt", content_text="dati altrui",
        )
        _company()  # il tenant della richiesta esiste ma non possiede il file
        request = _request(rf_with_tenant, "post", other_file.pk)

        with pytest.raises(Http404):
            views.onboarding_file_delete(request, pk=other_file.pk)
        assert CompanyFile.objects.filter(pk=other_file.pk).exists()

    def test_owner_can_delete_with_post(self, rf_with_tenant):
        company = _company()
        company_file = CompanyFile.objects.create(
            company=company, original_name="listino.txt", content_text="prezzi",
        )
        request = _request(rf_with_tenant, "post", company_file.pk)
        request.META["HTTP_HX_REQUEST"] = "true"

        response = views.onboarding_file_delete(request, pk=company_file.pk)

        assert response.status_code == 200
        assert not CompanyFile.objects.filter(pk=company_file.pk).exists()
