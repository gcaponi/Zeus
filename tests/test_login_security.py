"""Throttle sul login pubblico (finding #14)."""
from contextlib import nullcontext
from unittest.mock import Mock

import pytest
from django.core.cache import cache
from django.test import Client, override_settings

from apps.core import views as core_views
from apps.core.models import WorkspaceAccess

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    cache.clear()
    yield
    cache.clear()


@override_settings(
    ROOT_URLCONF="config.urls",
    LOGIN_RATE_LIMIT_GLOBAL=1,
    LOGIN_RATE_LIMIT_IP=1,
    LOGIN_RATE_LIMIT_EMAIL=1,
    LOGIN_RATE_LIMIT_WINDOW_SECONDS=900,
)
def test_login_rate_limit_blocks_before_authenticate(monkeypatch):
    monkeypatch.setattr(
        core_views, "schema_context", Mock(return_value=nullcontext()),
    )
    authenticate = Mock(return_value=None)
    monkeypatch.setattr(core_views, "authenticate", authenticate)
    WorkspaceAccess.objects.create(
        email="owner@example.com",
        tenant_domain="app.zeus.cais.uno",
    )
    client = Client()
    payload = {"login": "owner@example.com", "password": "wrong-password"}

    first = client.post("/accounts/login/", payload)
    second = client.post("/accounts/login/", payload)

    assert first.status_code == 200
    assert b"Email o password non validi." in first.content
    assert authenticate.call_count == 1
    assert second.status_code == 429
    assert second["Retry-After"] == "900"
    assert b"Troppi tentativi di accesso" in second.content
    assert authenticate.call_count == 1


@override_settings(ROOT_URLCONF="config.urls")
def test_login_rate_limit_fail_closed(monkeypatch):
    monkeypatch.setattr(
        core_views,
        "_login_rate_limit_allows",
        Mock(side_effect=RuntimeError("cache down")),
    )
    authenticate = Mock(return_value=None)
    monkeypatch.setattr(core_views, "authenticate", authenticate)
    client = Client()

    response = client.post(
        "/accounts/login/",
        {"login": "owner@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 503
    assert b"temporaneamente non disponibile" in response.content
    authenticate.assert_not_called()
