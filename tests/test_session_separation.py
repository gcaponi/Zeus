"""Sessioni separate per la zona admin: il login admin non slogga più l'app.

Regressione: con una sessione condivisa su tutto `.zeus.cais.uno`, il login
admin (public schema) ruotava la chiave di sessione e scriveva l'utente del
public schema; l'app (tenant schema) non risolveva più quell'utente
(pk collision + session auth hash mismatch) e sloggava l'utente.
"""
import pytest
from django.conf import settings
from django.test import Client, RequestFactory, override_settings

from apps.core.middleware import _session_cookie_domain, _session_cookie_name

pytestmark = pytest.mark.django_db


def test_app_session_cookie_name_was_rotated():
    """Il vecchio `sessionid` parent-domain non deve essere piu' letto."""
    assert settings.SESSION_COOKIE_NAME == "zeus_app_sessionid"


# --- Selezione del nome cookie per zona -------------------------------------------------


def test_admin_zone_uses_dedicated_cookie_name():
    assert (
        _session_cookie_name(RequestFactory().get("/admin/login/"))
        == settings.ADMIN_SESSION_COOKIE_NAME
    )
    assert (
        _session_cookie_name(RequestFactory().get("/zeus-admin/clients/"))
        == settings.ADMIN_SESSION_COOKIE_NAME
    )


def test_app_zone_uses_default_cookie_name():
    assert (
        _session_cookie_name(RequestFactory().get("/products/"))
        == settings.SESSION_COOKIE_NAME
    )
    assert (
        _session_cookie_name(RequestFactory().get("/accounts/login/"))
        == settings.SESSION_COOKIE_NAME
    )
    assert (
        _session_cookie_name(RequestFactory().get("/dashboard/"))
        == settings.SESSION_COOKIE_NAME
    )


# --- Integrazione: login app + login admin coesistono -----------------------------------


def _create_app_user():
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(
        username="app@x.it",
        email="app@x.it",
        password="pw",
    )
    from apps.core.models import WorkspaceAccess

    WorkspaceAccess.objects.create(
        email="app@x.it",
        tenant_domain="app.zeus.cais.uno",
    )
    return user


@override_settings(ROOT_URLCONF="config.urls")
def test_app_login_issues_handoff_not_session_cookie(monkeypatch):
    """Sessioni host-only: il login pubblico NON crea piu' la sessione app —
    emette un LoginHandoff monouso; la sessione nasce sul tenant host."""
    from contextlib import nullcontext
    from unittest.mock import Mock

    from apps.core import views as core_views
    from apps.core.models import LoginHandoff

    # sqlite di test non ha gli schema tenant: schema_context e' no-op.
    monkeypatch.setattr(
        core_views, "schema_context", Mock(return_value=nullcontext()),
    )

    user = _create_app_user()
    client = Client()

    response = client.post(
        "/accounts/login/",
        {"login": "app@x.it", "password": "pw"},
    )

    assert response.status_code == 302
    assert response.url.startswith("https://app.zeus.cais.uno/accounts/handoff/?t=")
    assert settings.SESSION_COOKIE_NAME not in client.cookies
    assert settings.ADMIN_SESSION_COOKIE_NAME not in client.cookies
    handoff = LoginHandoff.objects.get()
    assert handoff.tenant_schema == "app"
    assert handoff.user_id == user.pk
    assert handoff.consumed_at is None


def test_admin_login_does_not_logout_app():
    """Il login admin ruota solo il cookie admin: la sessione app resta viva."""
    user = _create_app_user()
    from django.contrib.auth import get_user_model

    get_user_model().objects.create_superuser("root", "root@x.it", "pw")
    client = Client()

    # Il login app completo e' coperto dai test handoff: qui basta una
    # sessione app valida (force_login) per verificare la separazione.
    client.force_login(user)
    app_session = client.cookies[settings.SESSION_COOKIE_NAME].value
    assert client.session["_auth_user_id"] == str(user.pk)

    response = client.post(
        "/admin/login/",
        {"username": "root", "password": "pw"},
    )
    assert response.status_code == 302
    # la sessione app NON è stata ruotata né invalidata
    assert client.cookies[settings.SESSION_COOKIE_NAME].value == app_session
    assert settings.ADMIN_SESSION_COOKIE_NAME in client.cookies
    assert (
        client.cookies[settings.ADMIN_SESSION_COOKIE_NAME].value != app_session
    )
    # l'app è ancora autenticata dopo il login admin
    assert client.session["_auth_user_id"] == str(user.pk)


def test_app_login_does_not_invalidate_admin_session():
    _create_app_user()
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    user_model.objects.create_superuser("root", "root@x.it", "pw")
    app_user = user_model.objects.get(username="app@x.it")
    client = Client()

    client.post("/admin/login/", {"username": "root", "password": "pw"})
    admin_session = client.cookies[settings.ADMIN_SESSION_COOKIE_NAME].value

    client.force_login(app_user)

    assert (
        client.cookies[settings.ADMIN_SESSION_COOKIE_NAME].value
        == admin_session
    )


# --- Codex Security finding 1: il cookie admin e' host-only -----------------


@override_settings(SESSION_COOKIE_DOMAIN=".zeus.cais.uno")
def test_admin_session_cookie_is_host_only():
    """Anche con Domain condiviso per la sessione app, il cookie della zona
    admin non deve avere attributo Domain: il browser non lo inviera' mai ai
    tenant sibling."""
    from django.contrib.auth import get_user_model

    get_user_model().objects.create_superuser("root", "root@x.it", "pw")
    client = Client()

    response = client.post("/admin/login/", {"username": "root", "password": "pw"})

    assert response.status_code == 302
    assert settings.ADMIN_SESSION_COOKIE_NAME in client.cookies
    assert client.cookies[settings.ADMIN_SESSION_COOKIE_NAME]["domain"] == ""


@override_settings(SESSION_COOKIE_DOMAIN=None)
def test_all_session_cookies_are_host_only():
    """Fase 2: SESSION_COOKIE_DOMAIN rimossa — sia il cookie app sia quello
    admin sono host-only; il login attraversa gli host via LoginHandoff."""
    assert _session_cookie_domain(RequestFactory().get("/dashboard/")) is None
    assert _session_cookie_domain(RequestFactory().get("/accounts/login/")) is None
    assert _session_cookie_domain(RequestFactory().get("/admin/login/")) is None
    assert _session_cookie_domain(RequestFactory().get("/zeus-admin/clients/")) is None


@override_settings(ROOT_URLCONF="config.urls")
def test_logout_get_does_not_end_sessions():
    user = _create_app_user()
    from django.contrib.auth import get_user_model
    from django.contrib.sessions.models import Session

    get_user_model().objects.create_superuser("root", "root@x.it", "pw")
    client = Client()
    client.force_login(user)
    client.post("/admin/login/", {"username": "root", "password": "pw"})
    app_key = client.cookies[settings.SESSION_COOKIE_NAME].value
    admin_key = client.cookies[settings.ADMIN_SESSION_COOKIE_NAME].value

    response = client.get("/accounts/logout/")

    assert response.status_code == 200
    assert b"method=\"post\"" in response.content
    assert b"/accounts/logout/" in response.content
    assert client.cookies[settings.SESSION_COOKIE_NAME].value == app_key
    assert client.cookies[settings.ADMIN_SESSION_COOKIE_NAME].value == admin_key
    assert Session.objects.filter(session_key=app_key).exists()
    assert Session.objects.filter(session_key=admin_key).exists()


@override_settings(ROOT_URLCONF="config.urls")
def test_logout_clears_both_session_cookies():
    user = _create_app_user()
    from django.contrib.auth import get_user_model

    get_user_model().objects.create_superuser("root", "root@x.it", "pw")
    client = Client()

    # config.urls monta public_login (richiede django-tenants), quindi il
    # login app si prepara con force_login; l'admin login è via form.
    client.force_login(user)
    client.post("/admin/login/", {"username": "root", "password": "pw"})
    assert settings.SESSION_COOKIE_NAME in client.cookies
    assert settings.ADMIN_SESSION_COOKIE_NAME in client.cookies

    response = client.post("/accounts/logout/")

    assert response.status_code == 302
    # Il TestClient mantiene i cookie eliminati con valore vuoto (il browser
    # li rimuove davvero): verifichiamo che entrambe le sessioni siano morte.
    assert client.cookies[settings.SESSION_COOKIE_NAME].value == ""
    assert client.cookies[settings.ADMIN_SESSION_COOKIE_NAME].value == ""


@override_settings(ROOT_URLCONF="config.urls")
def test_logout_revokes_admin_session_store():
    user = _create_app_user()
    from django.contrib.auth import get_user_model
    from django.contrib.sessions.models import Session

    get_user_model().objects.create_superuser("root", "root@x.it", "pw")
    client = Client()
    client.force_login(user)
    client.post("/admin/login/", {"username": "root", "password": "pw"})
    app_key = client.cookies[settings.SESSION_COOKIE_NAME].value
    admin_key = client.cookies[settings.ADMIN_SESSION_COOKIE_NAME].value

    client.post("/accounts/logout/")

    assert not Session.objects.filter(session_key=app_key).exists()
    assert not Session.objects.filter(session_key=admin_key).exists()

    replay = Client()
    replay.cookies[settings.ADMIN_SESSION_COOKIE_NAME] = admin_key
    replayed = replay.get("/admin/")
    assert replayed.status_code == 302
    assert "/admin/login" in replayed.url


def test_templates_do_not_use_get_logout_links():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "templates"
    offenders = []
    for path in root.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        if "href=\"{% url 'account_logout' %}\"" in text:
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


@override_settings(ROOT_URLCONF="config.urls")
def test_logout_post_requires_csrf():
    user = _create_app_user()
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    response = client.post("/accounts/logout/")

    assert response.status_code == 403
