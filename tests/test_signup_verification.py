"""Verifica email prima di WorkspaceAccess e login (finding #13)."""
import re
from contextlib import nullcontext
from datetime import timedelta
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import pytest
from django.core import mail
from django.core.cache import cache
from django.test import Client as DjangoClient
from django.test import override_settings
from django.utils import timezone

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

PAYLOAD = {
    "email": "owner@example.com",
    "password1": "A-strong-password-2026",
    "password2": "A-strong-password-2026",
    "company_name": "Owner Company",
    "company_slug": "owner-company",
}


@pytest.fixture(autouse=True)
def _signup_isolation(monkeypatch):
    monkeypatch.setattr(Client, "auto_create_schema", False)
    monkeypatch.setattr(core_views, "schema_context", Mock(return_value=nullcontext()))


def _signup(client=None, payload=None):
    http = client or DjangoClient()
    return http.post("/accounts/signup/", payload or PAYLOAD)


def _token_from_mailbox(index=-1):
    match = re.search(r"https?://\S+", mail.outbox[index].body)
    assert match, mail.outbox[index].body
    return parse_qs(urlparse(match.group(0)).query)["t"][0]


@override_settings(ROOT_URLCONF="config.urls")
def test_uppercase_slug_provisions_lowercase_host():
    response = _signup(
        payload={
            **PAYLOAD,
            "email": "caps@example.com",
            "company_name": "Testes",
            "company_slug": "Testes",
        }
    )
    assert response.status_code == 200
    pending = SignupProvisioning.objects.get(email="caps@example.com")
    assert pending.slug == "testes"

    token = _token_from_mailbox()
    confirm = DjangoClient().get("/accounts/signup/confirm/", {"t": token})

    assert confirm.status_code == 302
    assert "testes.zeus.cais.uno/accounts/handoff/" in confirm.url
    assert Client.objects.filter(schema_name="testes").exists()
    assert not Client.objects.filter(schema_name="Testes").exists()
    assert Domain.objects.filter(domain="testes.zeus.cais.uno").exists()


@override_settings(ROOT_URLCONF="config.urls")
def test_unverified_signup_creates_no_workspace_or_access():
    response = _signup()

    assert response.status_code == 200
    assert b"Controlla la tua email" in response.content
    assert not Client.objects.filter(schema_name="owner-company").exists()
    assert not Domain.objects.filter(domain="owner-company.zeus.cais.uno").exists()
    assert not WorkspaceAccess.objects.filter(email="owner@example.com").exists()
    assert not WorkspaceSubscription.objects.exists()
    pending = SignupProvisioning.objects.get(slug="owner-company")
    assert pending.status == SignupProvisioning.STATUS_PENDING
    assert pending.password_hash
    assert pending.token_hash
    assert len(mail.outbox) == 1
    assert "Conferma il tuo account ZEUS" in mail.outbox[0].subject
    assert "owner@example.com" in mail.outbox[0].to


@override_settings(ROOT_URLCONF="config.urls")
def test_unverified_signup_cannot_log_in():
    _signup()

    login = DjangoClient().post(
        "/accounts/login/",
        {"login": "owner@example.com", "password": "A-strong-password-2026"},
    )

    assert login.status_code == 200
    assert b"Email o password non validi" in login.content
    assert not WorkspaceAccess.objects.exists()


@override_settings(ROOT_URLCONF="config.urls")
def test_confirm_creates_workspace_and_access():
    _signup()
    token = _token_from_mailbox()

    response = DjangoClient().get("/accounts/signup/confirm/", {"t": token})

    assert response.status_code == 302
    assert "owner-company.zeus.cais.uno/accounts/handoff/" in response.url
    assert Client.objects.filter(schema_name="owner-company").exists()
    assert WorkspaceAccess.objects.filter(
        email="owner@example.com",
        tenant_domain="owner-company.zeus.cais.uno",
    ).exists()
    pending = SignupProvisioning.objects.get(slug="owner-company")
    assert pending.status == SignupProvisioning.STATUS_COMPLETED
    assert pending.verified_at is not None
    assert pending.token_hash is None


@override_settings(ROOT_URLCONF="config.urls")
def test_confirm_token_cannot_be_replayed():
    _signup()
    token = _token_from_mailbox()
    first = DjangoClient().get("/accounts/signup/confirm/", {"t": token})
    second = DjangoClient().get("/accounts/signup/confirm/", {"t": token})

    assert first.status_code == 302
    assert second.status_code == 400
    assert Client.objects.filter(schema_name="owner-company").count() == 1


@override_settings(ROOT_URLCONF="config.urls")
def test_expired_confirm_token_is_rejected():
    _signup()
    row = SignupProvisioning.objects.get(slug="owner-company")
    row.expires_at = timezone.now() - timedelta(seconds=1)
    row.save(update_fields=["expires_at"])
    token = _token_from_mailbox()

    response = DjangoClient().get("/accounts/signup/confirm/", {"t": token})

    assert response.status_code == 400
    assert not WorkspaceAccess.objects.exists()


@override_settings(ROOT_URLCONF="config.urls")
def test_duplicate_pending_email_resends_and_does_not_provision():
    _signup()
    first = SignupProvisioning.objects.get()
    first_hash = first.token_hash

    second = _signup()

    assert second.status_code == 200
    assert SignupProvisioning.objects.count() == 1
    first.refresh_from_db()
    assert first.token_hash != first_hash
    assert not Client.objects.exists()
    assert len(mail.outbox) == 2


@override_settings(ROOT_URLCONF="config.urls")
def test_login_after_confirm_resolves_workspace_access(monkeypatch):
    _signup()
    token = _token_from_mailbox()
    DjangoClient().get("/accounts/signup/confirm/", {"t": token})
    monkeypatch.setattr(core_views, "authenticate", Mock(return_value=Mock(pk=1, is_active=True)))
    monkeypatch.setattr(core_views, "_create_login_handoff", Mock(return_value="handoff-token"))

    login = DjangoClient().post(
        "/accounts/login/",
        {"login": "owner@example.com", "password": "ignored-after-mock"},
    )

    assert login.status_code == 302
    assert "owner-company.zeus.cais.uno/accounts/handoff/" in login.url
