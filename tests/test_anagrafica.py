import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import HttpResponse
from django.test import override_settings
from django.urls import reverse

from apps.companies import views
from apps.companies.forms import (
    CompanyAnagraficaForm,
    ContactValueForm,
    PhoneFormSet,
    SocialFormSet,
    SocialProfileForm,
    WhatsAppFormSet,
)
from apps.companies.models import Company, CompanyContact, CompanySocial
from apps.core.middleware import CompanyAnagraficaRequiredMiddleware


def _complete_company(company):
    company.nome_commerciale = "Esempio"
    company.partita_iva = "12345678901"
    company.codice_fiscale = "ABCDEF12G34H567I"
    company.rea = "MI-1234567"
    company.pec = "esempio@pec.it"
    company.email_contatto = "info@esempio.it"
    company.sito_web = "https://esempio.it"
    company.save()
    CompanyContact.objects.create(
        company=company,
        kind=CompanyContact.KIND_PHONE,
        value="+39 02 1234567",
    )
    CompanyContact.objects.create(
        company=company,
        kind=CompanyContact.KIND_WHATSAPP,
        value="+39 333 1234567",
    )
    CompanySocial.objects.create(
        company=company,
        network="LinkedIn",
        url="https://linkedin.com/company/esempio",
    )
    return company


def _valid_payload():
    return {
        "name": "Esempio S.r.l.",
        "nome_commerciale": "Esempio",
        "partita_iva": "12345678901",
        "codice_fiscale": "ABCDEF12G34H567I",
        "rea": "mi-1234567",
        "pec": "esempio@pec.it",
        "email_contatto": "info@esempio.it",
        "sito_web": "esempio.it",
        "phones-TOTAL_FORMS": "1",
        "phones-INITIAL_FORMS": "0",
        "phones-MIN_NUM_FORMS": "1",
        "phones-MAX_NUM_FORMS": "1000",
        "phones-0-value": "+39 02 1234567",
        "whatsapp-TOTAL_FORMS": "1",
        "whatsapp-INITIAL_FORMS": "0",
        "whatsapp-MIN_NUM_FORMS": "1",
        "whatsapp-MAX_NUM_FORMS": "1000",
        "whatsapp-0-value": "+39 333 1234567",
        "socials-TOTAL_FORMS": "1",
        "socials-INITIAL_FORMS": "0",
        "socials-MIN_NUM_FORMS": "1",
        "socials-MAX_NUM_FORMS": "1000",
        "socials-0-network": "LinkedIn",
        "socials-0-url": "https://linkedin.com/company/esempio",
        "next": "/onboarding/",
    }


@pytest.mark.django_db
class TestCompanyAnagrafica:
    def test_every_visible_field_has_format_guidance(self):
        forms = (CompanyAnagraficaForm(), ContactValueForm(), SocialProfileForm())

        for form in forms:
            for field in form.visible_fields():
                attrs = field.field.widget.attrs
                assert attrs["placeholder"].startswith("Es. "), field.name
                assert attrs["title"].startswith("Esempio corretto: "), field.name
                assert attrs["data-input-mask"], field.name
                assert attrs["data-format-example"], field.name

    @pytest.mark.parametrize(
        ("field_name", "invalid_value"),
        [
            ("name", ""),
            ("nome_commerciale", ""),
            ("partita_iva", "1234567890"),
            ("codice_fiscale", "ABC!"),
            ("rea", "123456"),
            ("pec", "pec-non-valida"),
            ("email_contatto", "email-non-valida"),
            ("sito_web", "sito non valido"),
        ],
    )
    def test_every_company_field_error_contains_a_correct_example(
        self, field_name, invalid_value
    ):
        payload = _valid_payload()
        payload[field_name] = invalid_value

        form = CompanyAnagraficaForm(data=payload)

        assert form.is_valid() is False
        assert "Esempio:" in " ".join(form.errors[field_name])

    @pytest.mark.parametrize(
        ("form_class", "payload", "field_name"),
        [
            (ContactValueForm, {"value": "numero-non-valido"}, "value"),
            (SocialProfileForm, {"network": "", "url": "https://example.it"}, "network"),
            (SocialProfileForm, {"network": "LinkedIn", "url": "link non valido"}, "url"),
        ],
    )
    def test_every_repeatable_field_error_contains_a_correct_example(
        self, form_class, payload, field_name
    ):
        form = form_class(data=payload)

        assert form.is_valid() is False
        assert "Esempio:" in " ".join(form.errors[field_name])

    def test_empty_repeatable_formsets_render_one_required_row(self):
        assert PhoneFormSet(prefix="phones").total_form_count() == 1
        assert WhatsAppFormSet(prefix="whatsapp").total_form_count() == 1
        assert SocialFormSet(prefix="socials").total_form_count() == 1

    def test_completion_requires_scalars_contacts_and_social(self):
        company = Company.objects.create(schema_name="test-tenant", name="Esempio S.r.l.")
        assert company.has_complete_anagrafica() is False
        _complete_company(company)
        assert company.has_complete_anagrafica() is True

    @override_settings(ROOT_URLCONF="config.urls")
    def test_valid_form_saves_repeatable_contacts_and_redirects(self, rf_with_tenant):
        Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        request = rf_with_tenant(
            "post",
            reverse("company-anagrafica"),
            _valid_payload(),
            form=True,
        )
        request._messages = FallbackStorage(request)

        response = views.company_anagrafica(request)

        company = Company.objects.get(schema_name="test-tenant")
        assert response.status_code == 302
        assert response["Location"] == "/onboarding/"
        assert company.sito_web == "https://esempio.it"
        assert company.rea == "MI-1234567"
        assert company.has_complete_anagrafica() is True
        assert company.contacts.filter(kind=CompanyContact.KIND_PHONE).count() == 1
        assert company.contacts.filter(kind=CompanyContact.KIND_WHATSAPP).count() == 1
        assert company.social_profiles.get().network == "LinkedIn"

    @override_settings(ROOT_URLCONF="config.urls")
    def test_missing_required_repeatable_field_returns_400(self, rf_with_tenant):
        Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        payload = _valid_payload()
        payload["phones-0-value"] = ""
        request = rf_with_tenant(
            "post",
            reverse("company-anagrafica"),
            payload,
            form=True,
        )
        request._messages = FallbackStorage(request)

        response = views.company_anagrafica(request)

        assert response.status_code == 400
        assert b"Telefono:" in response.content
        assert Company.objects.get(schema_name="test-tenant").has_complete_anagrafica() is False

    @override_settings(ROOT_URLCONF="config.urls")
    def test_removed_extra_row_stays_hidden_after_validation_error(self, rf_with_tenant):
        Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        payload = _valid_payload()
        payload["name"] = ""
        payload["phones-TOTAL_FORMS"] = "2"
        payload["phones-1-value"] = "+39 02 9999999"
        payload["phones-1-DELETE"] = "on"
        request = rf_with_tenant(
            "post",
            reverse("company-anagrafica"),
            payload,
            form=True,
        )
        request._messages = FallbackStorage(request)

        response = views.company_anagrafica(request)
        html = response.content.decode()

        assert response.status_code == 400
        assert 'data-form-index="1" hidden' in html
        assert 'name="phones-1-DELETE"' in html
        assert "checked" in html

    @override_settings(ROOT_URLCONF="config.urls")
    def test_onboarding_prefills_and_syncs_company_website(self, rf_with_tenant):
        company = _complete_company(
            Company.objects.create(schema_name="test-tenant", name="Esempio S.r.l.")
        )
        context = views._source_form_context(company)
        assert context["source_url"] == "https://esempio.it"

        payload = {
            "url": "nuovo-esempio.it",
            "company_notes": "Nota stabile.",
        }
        request = rf_with_tenant(
            "post",
            reverse("onboarding-source-create"),
            payload,
            form=True,
        )
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(views.run_pipeline, "delay", lambda *args, **kwargs: None)
            response = views.onboarding_source_create(request)

        company.refresh_from_db()
        assert response.status_code == 302
        assert company.sito_web == "https://nuovo-esempio.it"


@pytest.mark.django_db
class TestCompanyAnagraficaGate:
    def _middleware(self):
        return CompanyAnagraficaRequiredMiddleware(lambda request: HttpResponse("ok"))

    def test_incomplete_tenant_is_redirected_with_next(self, rf_with_tenant):
        Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        request = rf_with_tenant("get", "/products/?page=2")

        response = self._middleware()(request)

        assert response.status_code == 302
        assert response["Location"].startswith("/company/anagrafica/?next=")

    def test_profile_page_is_always_allowed(self, rf_with_tenant):
        Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        request = rf_with_tenant("get", "/company/anagrafica/")
        assert self._middleware()(request).status_code == 200

    def test_complete_tenant_can_access_workspace(self, rf_with_tenant):
        _complete_company(
            Company.objects.create(schema_name="test-tenant", name="Esempio S.r.l.")
        )
        request = rf_with_tenant("get", "/dashboard/")
        assert self._middleware()(request).status_code == 200

    def test_incomplete_api_returns_machine_readable_conflict(self, rf_with_tenant):
        Company.objects.create(schema_name="test-tenant", name="Test Tenant")
        request = rf_with_tenant("get", "/api/company/")
        request.META["HTTP_ACCEPT"] = "application/json"

        response = self._middleware()(request)

        assert response.status_code == 409
        assert b"company_profile_required" in response.content
