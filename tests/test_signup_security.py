from contextlib import nullcontext
from unittest.mock import Mock

import pytest
from django.core.cache import cache
from django.test import Client as DjangoClient
from django.test import RequestFactory, override_settings

from apps.core import views as core_views
from apps.core.models import (
    Client,
    Domain,
    SignupProvisioning,
    WorkspaceAccess,
    WorkspaceSubscription,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    cache.clear()
    yield
    cache.clear()


@override_settings(
    ROOT_URLCONF="config.urls",
    SIGNUP_RATE_LIMIT_GLOBAL=1,
    SIGNUP_RATE_LIMIT_IP=1,
    SIGNUP_RATE_LIMIT_EMAIL=1,
)
def test_signup_rate_limit_runs_before_provisioning():
    client = DjangoClient()
    payload = {
        "email": "bot@example.com",
        "password1": "A-strong-password-2026",
        "password2": "A-strong-password-2026",
        "company_name": "Bot Company",
        "company_slug": "bot-company",
    }

    first = client.post("/accounts/signup/", {"email": "invalid"})
    second = client.post("/accounts/signup/", payload)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second["Retry-After"] == "3600"
    assert not Client.objects.filter(schema_name="bot-company").exists()
    assert not SignupProvisioning.objects.exists()


def test_failed_provisioning_removes_all_partial_artifacts(monkeypatch):
    monkeypatch.setattr(Client, "auto_create_schema", False)
    monkeypatch.setattr(core_views, "schema_context", Mock(return_value=nullcontext()))
    request = RequestFactory().post("/accounts/signup/", REMOTE_ADDR="192.0.2.10")
    form = Mock()
    form.cleaned_data = {
        "email": "owner@example.com",
        "company_name": "Failure Company",
        "company_slug": "failure-company",
    }
    form.save.side_effect = RuntimeError("forced user creation failure")
    provisioning = SignupProvisioning.objects.create(
        slug="failure-company",
        email="owner@example.com",
        client_ip_hash="hash",
        status=SignupProvisioning.STATUS_PENDING,
    )

    with pytest.raises(RuntimeError):
        core_views._provision_signup(request, form, provisioning)

    provisioning.refresh_from_db()
    assert provisioning.status == SignupProvisioning.STATUS_FAILED
    assert provisioning.cleanup_required is False
    assert provisioning.error_code == "RuntimeError"
    assert not Client.objects.filter(schema_name="failure-company").exists()
    assert not Domain.objects.filter(domain="failure-company.zeus.cais.uno").exists()
    assert not WorkspaceAccess.objects.filter(email="owner@example.com").exists()
    assert not WorkspaceSubscription.objects.exists()


def test_pending_provisioning_cannot_be_claimed_twice():
    SignupProvisioning.objects.create(
        slug="claimed",
        email="first@example.com",
        client_ip_hash="hash",
        status=SignupProvisioning.STATUS_PENDING,
    )

    duplicate = core_views._claim_signup_provisioning(
        "claimed",
        "second@example.com",
        "192.0.2.20",
    )

    assert duplicate is None
    assert SignupProvisioning.objects.get(slug="claimed").email == "first@example.com"
