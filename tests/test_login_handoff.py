"""Test per il login handoff (Codex Security finding 1 — chiusura completa).

Con le sessioni host-only il login avviene in due tempi: public_login
autentica sul public host ed emette un token monouso da 60s; login_handoff
lo consuma sull'host del tenant e apre li' la sessione. Il token e'
usa-e-getta (consumo atomico), scaduto a breve, e legato al tenant emesso.
"""
import hashlib
from contextlib import nullcontext
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.core import signing
from django.test import RequestFactory
from django.utils import timezone

from apps.core import views as core_views
from apps.core.models import LoginHandoff
from apps.core.views import _create_login_handoff, login_handoff

pytestmark = pytest.mark.django_db


class FakeTenant:
    schema_name = "app"
    name = "App Tenant"


def _handoff_request(token=""):
    request = RequestFactory().get("/accounts/handoff/", {"t": token})
    request.tenant = FakeTenant()
    request.session = SessionStore()
    return request


def _user(django_user_model):
    return django_user_model.objects.create_user(
        username="app@x.it", email="app@x.it", password="pw",
    )


class TestLoginHandoff:
    @pytest.fixture(autouse=True)
    def _urls(self, settings, monkeypatch):
        # account/login.html (pagina di errore handoff) risolve route che
        # esistono solo nella urlconf completa, non in config.test_urls.
        settings.ROOT_URLCONF = "config.urls"
        # sqlite di test non ha gli schema tenant: schema_context e' no-op.
        monkeypatch.setattr(
            core_views, "schema_context", Mock(return_value=nullcontext()),
        )

    def test_valid_token_opens_tenant_session(self, django_user_model):
        user = _user(django_user_model)
        raw_token = _create_login_handoff("app", user)

        response = login_handoff(_handoff_request(raw_token))

        assert response.status_code == 302
        assert response.url == "/dashboard/"
        handoff = LoginHandoff.objects.get()
        assert handoff.consumed_at is not None

    def test_session_authenticates_the_user(self, django_user_model):
        user = _user(django_user_model)
        raw_token = _create_login_handoff("app", user)
        request = _handoff_request(raw_token)

        login_handoff(request)

        assert request.session["_auth_user_id"] == str(user.pk)

    def test_token_replay_is_rejected(self, django_user_model):
        user = _user(django_user_model)
        raw_token = _create_login_handoff("app", user)

        first = login_handoff(_handoff_request(raw_token))
        second = login_handoff(_handoff_request(raw_token))

        assert first.status_code == 302
        assert second.status_code == 200  # login page con errore, niente sessione
        assert "scaduto" in second.content.decode()

    def test_expired_token_is_rejected(self, django_user_model):
        user = _user(django_user_model)
        raw_token = _create_login_handoff("app", user)
        LoginHandoff.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

        response = login_handoff(_handoff_request(raw_token))

        assert response.status_code == 200
        assert "scaduto" in response.content.decode()

    def test_token_for_another_tenant_is_rejected(self, django_user_model):
        user = _user(django_user_model)
        raw_token = _create_login_handoff("altro-tenant", user)

        response = login_handoff(_handoff_request(raw_token))

        assert response.status_code == 200
        assert "scaduto" in response.content.decode()
        # Il token resta non consumato: il legittimo proprietario puo' ancora usarlo.
        assert LoginHandoff.objects.get().consumed_at is None

    def test_missing_token_is_rejected(self):
        response = login_handoff(_handoff_request(""))
        assert response.status_code == 200

    def test_tampered_signature_is_rejected(self, django_user_model):
        user = _user(django_user_model)
        raw_token = _create_login_handoff("app", user)
        tampered = f"{raw_token[:-1]}{'a' if raw_token[-1] != 'a' else 'b'}"

        response = login_handoff(_handoff_request(tampered))

        assert response.status_code == 200
        assert LoginHandoff.objects.get().consumed_at is None

    def test_inactive_user_is_rejected_without_consuming_token(self, django_user_model):
        user = _user(django_user_model)
        raw_token = _create_login_handoff("app", user)
        user.is_active = False
        user.save(update_fields=["is_active"])

        response = login_handoff(_handoff_request(raw_token))

        assert response.status_code == 200
        assert LoginHandoff.objects.get().consumed_at is None

    def test_only_token_hash_is_stored(self, django_user_model):
        user = _user(django_user_model)
        raw_token = _create_login_handoff("app", user)
        handoff = LoginHandoff.objects.get()
        payload = signing.loads(
            raw_token,
            salt=core_views.LOGIN_HANDOFF_SALT,
            max_age=core_views.LOGIN_HANDOFF_TTL_SECONDS,
        )
        assert handoff.token_hash == hashlib.sha256(payload["nonce"].encode()).hexdigest()
        assert payload["tenant"] == "app"
        assert raw_token not in handoff.token_hash
