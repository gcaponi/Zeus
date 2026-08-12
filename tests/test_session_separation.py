"""Sessioni separate per la zona admin: il login admin non slogga più l'app.

Regressione: con una sessione condivisa su tutto `.zeus.cais.uno`, il login
admin (public schema) ruotava la chiave di sessione e scriveva l'utente del
public schema; l'app (tenant schema) non risolveva più quell'utente
(pk collision + session auth hash mismatch) e sloggava l'utente.
"""
import pytest
from django.conf import settings
from django.test import Client, RequestFactory, override_settings

from apps.core.middleware import _session_cookie_name

pytestmark = pytest.mark.django_db


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


def test_app_login_creates_default_session_cookie():
    user = _create_app_user()
    client = Client()

    response = client.post(
        "/accounts/login/",
        {"login": "app@x.it", "password": "pw"},
    )

    assert response.status_code == 302
    assert settings.SESSION_COOKIE_NAME in client.cookies
    assert settings.ADMIN_SESSION_COOKIE_NAME not in client.cookies
    # l'utente risulta autenticato (la vista dashboard senza tenant rende 404,
    # quindi l'autenticazione si verifica sulla sessione)
    assert client.session["_auth_user_id"] == str(user.pk)


def test_admin_login_does_not_logout_app():
    """Il login admin ruota solo admin_sessionid: la sessione app resta viva."""
    user = _create_app_user()
    from django.contrib.auth import get_user_model

    get_user_model().objects.create_superuser("root", "root@x.it", "pw")
    client = Client()

    client.post("/accounts/login/", {"login": "app@x.it", "password": "pw"})
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

    get_user_model().objects.create_superuser("root", "root@x.it", "pw")
    client = Client()

    client.post("/admin/login/", {"username": "root", "password": "pw"})
    admin_session = client.cookies[settings.ADMIN_SESSION_COOKIE_NAME].value

    client.post("/accounts/login/", {"login": "app@x.it", "password": "pw"})

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


@override_settings(SESSION_COOKIE_DOMAIN=".zeus.cais.uno")
def test_app_session_cookie_keeps_shared_domain():
    """La sessione app resta Domain-condivisa finche' il login avviene sul
    public host (fase 1): il fix host-only riguarda solo la zona admin."""
    _create_app_user()
    client = Client()

    response = client.post("/accounts/login/", {"login": "app@x.it", "password": "pw"})

    assert response.status_code == 302
    assert (
        client.cookies[settings.SESSION_COOKIE_NAME]["domain"]
        == ".zeus.cais.uno"
    )


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

    response = client.get("/accounts/logout/")

    assert response.status_code == 302
    # Il TestClient mantiene i cookie eliminati con valore vuoto (il browser
    # li rimuove davvero): verifichiamo che entrambe le sessioni siano morte.
    assert client.cookies[settings.SESSION_COOKIE_NAME].value == ""
    assert client.cookies[settings.ADMIN_SESSION_COOKIE_NAME].value == ""
