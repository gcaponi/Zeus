import pytest
from django.contrib import admin
from django.test import RequestFactory

from apps.core.models import Client, Domain, Plan, WorkspaceAccess, WorkspaceSubscription
from apps.core.views import WORKSPACE_COOKIE, public_onboarding_redirect


@pytest.fixture
def tenant_client(monkeypatch):
    monkeypatch.setattr(Client, "auto_create_schema", False)
    return Client.objects.create(schema_name="admin-test", name="Admin Test")


@pytest.mark.django_db
class TestPlans:
    def test_plan_default_values(self):
        starter, _ = Plan.objects.update_or_create(
            slug="starter",
            defaults=Plan.default_values("starter"),
        )
        professional, _ = Plan.objects.update_or_create(
            slug="professional",
            defaults=Plan.default_values("professional"),
        )
        enterprise, _ = Plan.objects.update_or_create(
            slug="enterprise",
            defaults=Plan.default_values("enterprise"),
        )

        assert starter.max_product_dnas == 5
        assert starter.name == "Foundation"
        assert starter.max_files_per_product == 2
        assert professional.name == "Professional"
        assert professional.max_product_dnas == 15
        assert professional.max_files_per_product == 5
        assert enterprise.name == "Legacy"
        assert enterprise.unlimited_product_dnas is True

    def test_plan_quota_helpers(self):
        plan = Plan.objects.create(
            name="Quota Test",
            slug="quota-test",
            max_company_files=5,
            max_product_dnas=5,
            max_files_per_product=2,
        )

        assert plan.allows_company_file_count(4) is True
        assert plan.allows_company_file_count(5) is False
        assert plan.allows_product_dna_count(4) is True
        assert plan.allows_product_dna_count(5) is False
        assert plan.allows_product_file_count(1) is True
        assert plan.allows_product_file_count(2) is False


@pytest.mark.django_db
class TestWorkspaceSubscription:
    def test_subscription_blocks_suspended_workspace(self, tenant_client):
        plan = Plan.get_default()
        subscription = WorkspaceSubscription.objects.create(
            client=tenant_client,
            plan=plan,
            status=WorkspaceSubscription.STATUS_SUSPENDED,
        )

        assert subscription.can_use_workspace() is False
        assert subscription.can_add_company_file() is False
        assert subscription.can_add_product_dna() is False

    def test_subscription_respects_plan_limits(self, tenant_client):
        plan = Plan.get_default()
        subscription = WorkspaceSubscription.objects.create(
            client=tenant_client,
            plan=plan,
            status=WorkspaceSubscription.STATUS_ACTIVE,
            company_files_used=5,
            product_dnas_used=4,
        )

        assert subscription.can_add_company_file() is False
        assert subscription.can_add_product_dna() is True
        assert subscription.can_add_product_file(1) is True
        assert subscription.can_add_product_file(2) is False


@pytest.mark.django_db
class TestCoreAdmin:
    def test_public_admin_models_registered(self):
        assert Client in admin.site._registry
        assert Plan in admin.site._registry
        assert WorkspaceAccess in admin.site._registry
        assert WorkspaceSubscription in admin.site._registry


@pytest.mark.django_db
class TestWorkspaceCookieRedirect:
    def test_valid_workspace_cookie_redirects_to_workspace(self, tenant_client):
        Domain.objects.create(
            domain="admin-test.zeus.cais.uno",
            tenant=tenant_client,
            is_primary=True,
        )
        request = RequestFactory().get("/onboarding/")
        request.COOKIES[WORKSPACE_COOKIE] = "admin-test.zeus.cais.uno"

        response = public_onboarding_redirect(request)

        assert response.status_code == 302
        assert response["Location"] == "https://admin-test.zeus.cais.uno/onboarding/"

    def test_invalid_workspace_cookie_redirects_to_login(self):
        request = RequestFactory().get("/onboarding/")
        request.COOKIES[WORKSPACE_COOKIE] = "old-workspace.zeus.cais.uno"

        response = public_onboarding_redirect(request)

        assert response.status_code == 302
        assert response["Location"] == "https://zeus.cais.uno/accounts/login/"
