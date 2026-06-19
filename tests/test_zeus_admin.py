import json
from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.test import Client as TestClient
from django.test import RequestFactory
from django.urls import reverse

from apps.companies.models import (
    Company,
    CompanyDNA,
    CompanyFile,
    LLMCall,
    PipelineRun,
    Product,
    ProductDNA,
    ProductFile,
)
from apps.core.models import Client as TenantClient
from apps.core.models import Domain, Plan, WorkspaceSubscription
from apps.zeus_admin import views

User = get_user_model()


@pytest.mark.django_db
class TestZeusAdminDashboard:
    def test_requires_staff_user(self):
        user = User.objects.create_user(
            username="normal",
            email="normal@example.com",
            password="pw",
        )
        client = TestClient()
        client.force_login(user)

        response = client.get(reverse("zeus-admin-dashboard"))

        assert response.status_code == 302

        response = client.get(reverse("zeus-admin-clients"))

        assert response.status_code == 302

        response = client.get(reverse("zeus-admin-client-detail", args=[1]))

        assert response.status_code == 302

    def test_staff_user_sees_real_dashboard_data(self, monkeypatch):
        monkeypatch.setattr(TenantClient, "auto_create_schema", False)
        staff = User.objects.create_user(
            username="staff",
            email="staff@example.com",
            password="pw",
            is_staff=True,
        )
        tenant = TenantClient.objects.create(
            schema_name="rossi-metalli",
            name="Rossi Metalli",
        )
        Domain.objects.create(
            tenant=tenant,
            domain="rossi.zeus.cais.uno",
            is_primary=True,
        )
        plan, _ = Plan.objects.update_or_create(
            slug=Plan.SLUG_STARTER,
            defaults=Plan.default_values(Plan.SLUG_STARTER),
        )
        WorkspaceSubscription.objects.create(
            client=tenant,
            plan=plan,
            status=WorkspaceSubscription.STATUS_ACTIVE,
        )
        company = Company.objects.create(
            schema_name="rossi-metalli",
            name="Rossi Metalli SRL",
        )
        CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={"chi_siamo": "Rossi"},
        )
        Product.objects.create(company=company, name="Canale X", slug="canale-x")
        PipelineRun.objects.create(
            company=company,
            status=PipelineRun.STATUS_COMPLETED,
            current_step="done",
        )
        LLMCall.objects.create(
            company=company,
            model_name="test-model",
            prompt_text="prompt",
            response_text="response",
            tokens_in=10,
            tokens_out=20,
            cost_usd=0.25,
            latency_ms=100,
        )
        request = RequestFactory().get(reverse("zeus-admin-dashboard"))
        request.user = staff

        response = views.dashboard(request)

        assert response.status_code == 200
        assert b"Dashboard" in response.content
        assert b"Rossi Metalli" in response.content
        assert b"rossi.zeus.cais.uno" in response.content
        assert b"Foundation" in response.content
        assert b"DNA completo" in response.content
        assert b"2500" in response.content
        assert b"Sistema" in response.content
        assert b"Database" in response.content
        assert b"Celery Worker" in response.content
        assert b"Storage" in response.content
        assert b"/zeus-admin/clients/?segment=active" in response.content
        assert b"/zeus-admin/clients/?segment=complete_dna" in response.content
        main_html = response.content.decode().split("<main", 1)[1]
        assert "Django Admin" not in main_html

    def test_system_health_in_context(self, monkeypatch):
        monkeypatch.setattr(TenantClient, "auto_create_schema", False)
        staff = User.objects.create_user(
            username="health-staff",
            email="health@example.com",
            password="pw",
            is_staff=True,
        )
        request = RequestFactory().get(reverse("zeus-admin-dashboard"))
        request.user = staff

        response = views.dashboard(request)
        html = response.content.decode()

        assert response.status_code == 200
        assert "Database" in html
        assert "Celery Worker" in html
        assert "Storage" in html
        assert "Uptime" in html
        assert "Online" in html

    def test_clients_page_filters_real_dashboard_data(self, monkeypatch):
        monkeypatch.setattr(TenantClient, "auto_create_schema", False)
        staff = User.objects.create_user(
            username="clients-staff",
            email="clients@example.com",
            password="pw",
            is_staff=True,
        )
        plan, _ = Plan.objects.update_or_create(
            slug=Plan.SLUG_STARTER,
            defaults=Plan.default_values(Plan.SLUG_STARTER),
        )
        active_tenant = TenantClient.objects.create(
            schema_name="rossi-metalli",
            name="Rossi Metalli",
        )
        Domain.objects.create(
            tenant=active_tenant,
            domain="rossi.zeus.cais.uno",
            is_primary=True,
        )
        WorkspaceSubscription.objects.create(
            client=active_tenant,
            plan=plan,
            status=WorkspaceSubscription.STATUS_ACTIVE,
        )
        suspended_tenant = TenantClient.objects.create(
            schema_name="bianchi-infissi",
            name="Bianchi Infissi",
        )
        WorkspaceSubscription.objects.create(
            client=suspended_tenant,
            plan=plan,
            status=WorkspaceSubscription.STATUS_SUSPENDED,
        )
        company = Company.objects.create(
            schema_name="rossi-metalli",
            name="Rossi Metalli SRL",
        )
        CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={"chi_siamo": "Rossi"},
        )
        request = RequestFactory().get(
            reverse("zeus-admin-clients"),
            {"segment": "active", "q": "rossi"},
        )
        request.user = staff

        response = views.clients(request)
        html = response.content.decode()

        assert response.status_code == 200
        assert "Clienti ZEUS" in html
        assert "Rossi Metalli" in html
        assert "Bianchi Infissi" not in html
        assert "1 di 2 workspace" in html
        main_html = html.split("<main", 1)[1]
        assert "Django Admin" not in main_html

    def test_clients_htmx_returns_results_partial(self, monkeypatch):
        monkeypatch.setattr(TenantClient, "auto_create_schema", False)
        staff = User.objects.create_user(
            username="htmx-staff",
            email="htmx@example.com",
            password="pw",
            is_staff=True,
        )
        request = RequestFactory().get(
            reverse("zeus-admin-clients"),
            HTTP_HX_REQUEST="true",
        )
        request.user = staff

        response = views.clients(request)
        html = response.content.decode()

        assert response.status_code == 200
        assert "Risultati Clienti" in html
        assert "Clienti ZEUS" not in html

    def test_client_detail_shows_configuration_and_uploaded_files(self, monkeypatch):
        monkeypatch.setattr(TenantClient, "auto_create_schema", False)
        staff = User.objects.create_user(
            username="detail-staff",
            email="detail@example.com",
            password="pw",
            is_staff=True,
        )
        tenant = TenantClient.objects.create(
            schema_name="rossi-metalli",
            name="Rossi Metalli",
        )
        Domain.objects.create(
            tenant=tenant,
            domain="rossi.zeus.cais.uno",
            is_primary=True,
        )
        plan, _ = Plan.objects.update_or_create(
            slug=Plan.SLUG_STARTER,
            defaults=Plan.default_values(Plan.SLUG_STARTER),
        )
        WorkspaceSubscription.objects.create(
            client=tenant,
            plan=plan,
            status=WorkspaceSubscription.STATUS_ACTIVE,
            notes="Cliente strategico",
        )
        company = Company.objects.create(
            schema_name="rossi-metalli",
            name="Rossi Metalli SRL",
        )
        company_dna = CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={"chi_siamo": "Rossi"},
        )
        CompanyFile.objects.create(
            company=company,
            original_name="Scheda tecnica aziendale.pdf",
            content_text="Contenuto scheda aziendale",
            file_size=2048,
            uploaded_by=staff,
        )
        product = Product.objects.create(
            company=company,
            name="Prodotto Alpha",
            slug="prodotto-alpha",
        )
        product_dna = ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_COMPLETE,
            content={"descrizione": "Alpha"},
        )
        ProductFile.objects.create(
            product=product,
            original_name="Manuale prodotto.pdf",
            content_text="Istruzioni del prodotto Alpha",
            file_size=4096,
            uploaded_by=staff,
        )
        request = RequestFactory().get(
            reverse("zeus-admin-client-detail", args=[tenant.pk]),
        )
        request.user = staff

        response = views.client_detail(request, tenant.pk)
        html = response.content.decode()

        assert response.status_code == 200
        assert "Configurazione Rapida" in html
        assert "Limiti Piano" in html
        assert "Rossi Metalli" in html
        assert "Scheda tecnica aziendale.pdf" in html
        assert "Prodotto Alpha" in html
        assert "Manuale prodotto.pdf" in html
        assert "Cambia Password" in html
        assert "Apri" in html
        assert "Elimina" in html
        assert "zeus-content-modal" in html
        assert "data-open-content" in html
        assert "Cambia Password" in html
        assert reverse("zeus-admin-company-dna-open", args=[tenant.pk, company_dna.pk]) in html
        assert reverse("zeus-admin-product-dna-open", args=[tenant.pk, product_dna.pk]) in html
        main_html = html.split("<main", 1)[1]
        assert "Django Admin" not in main_html

    def test_client_detail_updates_subscription_configuration(self, monkeypatch):
        monkeypatch.setattr(TenantClient, "auto_create_schema", False)
        staff = User.objects.create_user(
            username="config-staff",
            email="config@example.com",
            password="pw",
            is_staff=True,
        )
        foundation, _ = Plan.objects.update_or_create(
            slug=Plan.SLUG_STARTER,
            defaults=Plan.default_values(Plan.SLUG_STARTER),
        )
        professional, _ = Plan.objects.update_or_create(
            slug=Plan.SLUG_PROFESSIONAL,
            defaults=Plan.default_values(Plan.SLUG_PROFESSIONAL),
        )
        tenant = TenantClient.objects.create(
            schema_name="config-client",
            name="Config Client",
            on_trial=True,
        )
        WorkspaceSubscription.objects.create(
            client=tenant,
            plan=foundation,
            status=WorkspaceSubscription.STATUS_TRIAL,
        )
        request = RequestFactory().post(
            reverse("zeus-admin-client-detail", args=[tenant.pk]),
            {
                "plan_id": str(professional.pk),
                "status": WorkspaceSubscription.STATUS_SUSPENDED,
                "paid_until": "2026-07-01",
                "notes": "Upgrade sospeso in attesa pagamento",
            },
        )
        request.user = staff

        response = views.client_detail(request, tenant.pk)

        tenant.refresh_from_db()
        tenant.subscription.refresh_from_db()
        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/clients/{tenant.pk}/?saved=1")
        assert tenant.on_trial is False
        assert tenant.paid_until == date(2026, 7, 1)
        assert tenant.subscription.plan == professional
        assert tenant.subscription.status == WorkspaceSubscription.STATUS_SUSPENDED
        assert tenant.subscription.notes == "Upgrade sospeso in attesa pagamento"

    def test_app_domain_is_excluded_from_admin_clients(self, monkeypatch):
        monkeypatch.setattr(TenantClient, "auto_create_schema", False)
        staff = User.objects.create_user(
            username="exclude-staff",
            email="exclude@example.com",
            password="pw",
            is_staff=True,
        )
        app_tenant = TenantClient.objects.create(
            schema_name="zeus",
            name="Internal App Tenant",
        )
        Domain.objects.create(
            tenant=app_tenant,
            domain="zeus.cais.uno",
            is_primary=True,
        )
        client_tenant = TenantClient.objects.create(
            schema_name="cais",
            name="Cais",
        )
        Domain.objects.create(
            tenant=client_tenant,
            domain="cais.zeus.cais.uno",
            is_primary=True,
        )
        plan, _ = Plan.objects.update_or_create(
            slug=Plan.SLUG_STARTER,
            defaults=Plan.default_values(Plan.SLUG_STARTER),
        )
        WorkspaceSubscription.objects.create(
            client=app_tenant,
            plan=plan,
            status=WorkspaceSubscription.STATUS_ACTIVE,
        )
        WorkspaceSubscription.objects.create(
            client=client_tenant,
            plan=plan,
            status=WorkspaceSubscription.STATUS_ACTIVE,
        )
        Company.objects.create(schema_name="cais", name="Cais")
        dashboard_request = RequestFactory().get(reverse("zeus-admin-dashboard"))
        dashboard_request.user = staff
        clients_request = RequestFactory().get(reverse("zeus-admin-clients"))
        clients_request.user = staff

        dashboard_response = views.dashboard(dashboard_request)
        clients_response = views.clients(clients_request)

        assert dashboard_response.status_code == 200
        assert clients_response.status_code == 200
        dashboard_html = dashboard_response.content.decode()
        clients_html = clients_response.content.decode()
        assert ">zeus.cais.uno<" not in dashboard_html
        assert ">zeus.cais.uno<" not in clients_html
        assert "Internal App Tenant" not in dashboard_html
        assert "Internal App Tenant" not in clients_html
        assert "Tenant non leggibile" not in clients_html
        assert "1 workspace totali" in clients_html

    def test_admin_can_open_and_delete_uploaded_files(self, monkeypatch):
        monkeypatch.setattr(TenantClient, "auto_create_schema", False)
        staff = User.objects.create_user(
            username="file-staff",
            email="file@example.com",
            password="pw",
            is_staff=True,
        )
        tenant = TenantClient.objects.create(
            schema_name="file-client",
            name="File Client",
        )
        plan, _ = Plan.objects.update_or_create(
            slug=Plan.SLUG_STARTER,
            defaults=Plan.default_values(Plan.SLUG_STARTER),
        )
        subscription = WorkspaceSubscription.objects.create(
            client=tenant,
            plan=plan,
            status=WorkspaceSubscription.STATUS_ACTIVE,
            company_files_used=1,
        )
        company = Company.objects.create(
            schema_name="file-client",
            name="File Client SRL",
        )
        company_file = CompanyFile.objects.create(
            company=company,
            original_name="Company notes.txt",
            content_text="Contenuto aziendale completo",
            file_size=100,
            uploaded_by=staff,
        )
        product = Product.objects.create(
            company=company,
            name="Prodotto File",
            slug="prodotto-file",
        )
        product_file = ProductFile.objects.create(
            product=product,
            original_name="Product notes.txt",
            content_text="Contenuto prodotto completo",
            file_size=200,
            uploaded_by=staff,
        )
        open_company_request = RequestFactory().get(
            reverse("zeus-admin-company-file-open", args=[tenant.pk, company_file.pk]),
        )
        open_company_request.user = staff
        open_product_request = RequestFactory().get(
            reverse("zeus-admin-product-file-open", args=[tenant.pk, product_file.pk]),
        )
        open_product_request.user = staff

        company_response = views.open_company_file(
            open_company_request,
            tenant.pk,
            company_file.pk,
        )
        product_response = views.open_product_file(
            open_product_request,
            tenant.pk,
            product_file.pk,
        )
        company_payload = json.loads(company_response.content)
        product_payload = json.loads(product_response.content)

        assert company_response.status_code == 200
        assert company_payload["title"] == "Company notes.txt"
        assert company_payload["content"] == "Contenuto aziendale completo"
        assert company_payload["meta"].startswith("Allegato aziendale")
        assert product_response.status_code == 200
        assert product_payload["title"] == "Product notes.txt"
        assert product_payload["content"] == "Contenuto prodotto completo"
        assert product_payload["meta"].startswith("Allegato prodotto")

        delete_company_request = RequestFactory().post(
            reverse("zeus-admin-company-file-delete", args=[tenant.pk, company_file.pk]),
        )
        delete_company_request.user = staff
        delete_product_request = RequestFactory().post(
            reverse("zeus-admin-product-file-delete", args=[tenant.pk, product_file.pk]),
        )
        delete_product_request.user = staff

        company_delete_response = views.delete_company_file(
            delete_company_request,
            tenant.pk,
            company_file.pk,
        )
        product_delete_response = views.delete_product_file(
            delete_product_request,
            tenant.pk,
            product_file.pk,
        )

        subscription.refresh_from_db()
        assert company_delete_response.status_code == 302
        assert product_delete_response.status_code == 302
        assert not CompanyFile.objects.filter(pk=company_file.pk).exists()
        assert not ProductFile.objects.filter(pk=product_file.pk).exists()
        assert subscription.company_files_used == 0

    def test_admin_can_open_and_delete_dna_versions(self, monkeypatch):
        monkeypatch.setattr(TenantClient, "auto_create_schema", False)
        staff = User.objects.create_user(
            username="dna-staff",
            email="dna@example.com",
            password="pw",
            is_staff=True,
        )
        tenant = TenantClient.objects.create(
            schema_name="dna-client",
            name="DNA Client",
        )
        company = Company.objects.create(
            schema_name="dna-client",
            name="DNA Client SRL",
        )
        older_company_dna = CompanyDNA.objects.create(
            company=company,
            version=1,
            dna_type=CompanyDNA.TYPE_PRE,
            content={"chi_siamo": "Versione precedente"},
            is_current=False,
        )
        current_company_dna = CompanyDNA.objects.create(
            company=company,
            version=2,
            dna_type=CompanyDNA.TYPE_COMPLETE,
            content={"chi_siamo": "Versione corrente"},
        )
        product = Product.objects.create(
            company=company,
            name="Prodotto DNA",
            slug="prodotto-dna",
        )
        older_product_dna = ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_PRE,
            content={"descrizione": "Bozza"},
            is_current=False,
        )
        current_product_dna = ProductDNA.objects.create(
            product=product,
            version=2,
            dna_type=ProductDNA.TYPE_COMPLETE,
            content={"descrizione": "Corrente"},
        )
        open_company_request = RequestFactory().get(
            reverse("zeus-admin-company-dna-open", args=[tenant.pk, current_company_dna.pk]),
        )
        open_company_request.user = staff
        open_product_request = RequestFactory().get(
            reverse("zeus-admin-product-dna-open", args=[tenant.pk, current_product_dna.pk]),
        )
        open_product_request.user = staff

        company_response = views.open_company_dna(
            open_company_request,
            tenant.pk,
            current_company_dna.pk,
        )
        product_response = views.open_product_dna(
            open_product_request,
            tenant.pk,
            current_product_dna.pk,
        )
        company_payload = json.loads(company_response.content)
        product_payload = json.loads(product_response.content)

        assert company_response.status_code == 200
        assert company_payload["title"] == "DNA completo v2"
        assert company_payload["type"] == "dna"
        assert company_payload["text_fields"][0]["label"] == "Chi Siamo"
        assert company_payload["text_fields"][0]["text"] == "Versione corrente"
        assert product_response.status_code == 200
        assert product_payload["title"] == "Prodotto DNA · DNA completo v2"
        assert product_payload["type"] == "dna"
        assert product_payload["text_fields"][0]["text"] == "Corrente"

        delete_company_request = RequestFactory().post(
            reverse("zeus-admin-company-dna-delete", args=[tenant.pk, current_company_dna.pk]),
        )
        delete_company_request.user = staff
        delete_product_request = RequestFactory().post(
            reverse("zeus-admin-product-dna-delete", args=[tenant.pk, current_product_dna.pk]),
        )
        delete_product_request.user = staff

        company_delete_response = views.delete_company_dna(
            delete_company_request,
            tenant.pk,
            current_company_dna.pk,
        )
        product_delete_response = views.delete_product_dna(
            delete_product_request,
            tenant.pk,
            current_product_dna.pk,
        )

        older_company_dna.refresh_from_db()
        older_product_dna.refresh_from_db()
        assert company_delete_response.status_code == 302
        assert product_delete_response.status_code == 302
        assert not CompanyDNA.objects.filter(pk=current_company_dna.pk).exists()
        assert not ProductDNA.objects.filter(pk=current_product_dna.pk).exists()
        assert older_company_dna.is_current is True
        assert older_product_dna.is_current is True
