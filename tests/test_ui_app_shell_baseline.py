from types import SimpleNamespace

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import Http404
from django.middleware.csrf import CsrfViewMiddleware
from django.template.loader import render_to_string
from django.test import RequestFactory, override_settings
from django.urls import resolve, reverse

from apps.core.views import (
    WORKSPACE_COOKIE,
    app_shell_preview,
    public_login,
    tenant_dashboard,
    tenant_landing,
)


@override_settings(ROOT_URLCONF="config.urls")
@pytest.mark.parametrize(
    ("url_name", "expected_path"),
    [
        ("tenant-landing", "/"),
        ("account_login", "/accounts/login/"),
        ("account_logout", "/accounts/logout/"),
        ("tenant-dashboard", "/dashboard/"),
        ("app-shell-preview", "/__shell_preview/"),
        ("onboarding-index", "/onboarding/"),
        ("product-list-create", "/products/"),
        ("motore-b-report", "/company/dna/motore-b/"),
        ("consistency-report", "/company/dna/consistency/"),
    ],
)
def test_named_route_contract(url_name, expected_path):
    assert reverse(url_name) == expected_path


@override_settings(ROOT_URLCONF="config.urls")
def test_onboarding_path_keeps_current_resolver_precedence():
    match = resolve("/onboarding/")

    assert match.url_name == "onboarding-index"


@override_settings(ROOT_URLCONF="config.urls")
def test_app_shell_preview_is_disabled_by_default():
    assert settings.ZEUS_APP_SHELL_ENABLED is False
    request = RequestFactory().get(reverse("app-shell-preview"))

    with pytest.raises(Http404):
        app_shell_preview(request)


@override_settings(ROOT_URLCONF="config.urls", ZEUS_APP_SHELL_ENABLED=True)
def test_app_shell_preview_renders_declared_slots():
    request = RequestFactory().get(reverse("app-shell-preview"))
    request.user = AnonymousUser()

    response = app_shell_preview(request)

    assert response.status_code == 200
    assert b'data-app-shell="v1"' in response.content
    assert b'id="app-sidebar"' in response.content
    assert b'id="app-header"' in response.content
    assert b'id="app-main"' in response.content
    assert b"Feature flag attivo" in response.content


@override_settings(ROOT_URLCONF="config.urls", ZEUS_APP_SHELL_ENABLED=True)
def test_app_shell_flag_does_not_change_public_pages():
    request_factory = RequestFactory()
    login_request = request_factory.get(reverse("account_login"))
    login_request.user = AnonymousUser()
    landing_request = request_factory.get(reverse("tenant-landing"))
    landing_request.user = AnonymousUser()

    responses = [
        public_login(login_request),
        tenant_landing(landing_request),
    ]

    assert all(response.status_code == 200 for response in responses)
    assert all(b'id="app-shell"' not in response.content for response in responses)


@override_settings(ROOT_URLCONF="config.urls", ZEUS_APP_SHELL_ENABLED=True)
def test_dashboard_uses_app_shell_when_flag_enabled():
    request = RequestFactory().get(reverse("tenant-dashboard"))
    request.user = SimpleNamespace(
        is_authenticated=True,
        email="ui-baseline@example.com",
    )
    request.tenant = SimpleNamespace(
        name="UI Baseline",
        schema_name="ui-baseline",
    )

    response = tenant_dashboard(request)

    assert response.status_code == 200
    assert b'id="app-shell"' in response.content
    assert b"zeus-app-shell--tenant" in response.content
    assert b"UI Baseline" in response.content
    assert reverse("onboarding-index").encode() in response.content
    assert reverse("product-list-create").encode() in response.content


@override_settings(ROOT_URLCONF="config.urls", ZEUS_APP_SHELL_ENABLED=True)
def test_tenant_shell_exposes_navigation_command_palette():
    request = RequestFactory().get(reverse("tenant-dashboard"))
    request.user = SimpleNamespace(
        is_authenticated=True,
        email="ui-baseline@example.com",
    )
    request.tenant = SimpleNamespace(
        name="UI Baseline",
        schema_name="ui-baseline",
    )

    response = tenant_dashboard(request)

    assert response.status_code == 200
    assert b'data-command-open' in response.content
    assert b'aria-label="Apri navigazione rapida"' in response.content
    assert b'id="zeus-command-palette"' in response.content
    assert b'role="dialog"' in response.content
    assert b'aria-modal="true"' in response.content
    assert b'data-command-input' in response.content
    for url_name in (
        "tenant-dashboard",
        "onboarding-index",
        "product-list-create",
        "motore-b-report",
        "consistency-report",
    ):
        assert reverse(url_name).encode() in response.content


def test_shared_generation_progress_exposes_semantic_states():
    running = render_to_string(
        "core/partials/_generation_progress.html",
        {"status": "running", "title": "Elaborazione"},
    )
    failed = render_to_string(
        "core/partials/_generation_progress.html",
        {"status": "failed", "error_msg": "Servizio non disponibile"},
    )
    completed = render_to_string(
        "core/partials/_generation_progress.html",
        {"status": "completed"},
    )

    assert 'data-app-state="loading"' in running
    assert 'role="status"' in running
    assert 'aria-live="polite"' in running
    assert 'data-app-state="error"' in failed
    assert 'role="alert"' in failed
    assert 'data-app-state="completed"' in completed
    assert 'role="status"' in completed


def test_product_list_exposes_semantic_empty_and_error_states():
    empty = render_to_string(
        "core/partials/product_list_content.html",
        {"products": []},
    )
    error = render_to_string(
        "core/partials/product_list_content.html",
        {"products": [], "error": "Specialista già presente"},
    )

    assert 'data-app-state="empty"' in empty
    assert 'data-app-state="error"' in error
    assert 'role="alert"' in error


@override_settings(ROOT_URLCONF="config.urls")
def test_public_pages_render_without_tenant_shell():
    request_factory = RequestFactory()
    login_request = request_factory.get(reverse("account_login"))
    login_request.user = AnonymousUser()
    landing_request = request_factory.get(reverse("tenant-landing"))
    landing_request.user = AnonymousUser()

    login_response = public_login(login_request)
    landing_response = tenant_landing(landing_request)

    assert login_response.status_code == 200
    assert b'name="csrfmiddlewaretoken"' in login_response.content
    assert b'id="app-main"' not in login_response.content
    assert landing_response.status_code == 200
    assert b'id="app-main"' not in landing_response.content


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="config.urls", ZEUS_APP_SHELL_ENABLED=False)
def test_dashboard_keeps_authentication_and_navigation_contract(django_user_model):
    request_factory = RequestFactory()
    anonymous_request = request_factory.get(reverse("tenant-dashboard"))
    anonymous_request.user = AnonymousUser()
    anonymous_response = tenant_dashboard(anonymous_request)

    assert anonymous_response.status_code == 302
    assert anonymous_response.url.startswith(reverse("account_login"))

    user = django_user_model.objects.create_user(
        username="ui-baseline",
        email="ui-baseline@example.com",
        password="test-password",
    )
    authenticated_request = request_factory.get(reverse("tenant-dashboard"))
    authenticated_request.user = user
    authenticated_request.tenant = SimpleNamespace(
        name="UI Baseline",
        schema_name="ui-baseline",
    )
    authenticated_response = tenant_dashboard(authenticated_request)

    assert authenticated_response.status_code == 200
    assert b"Inizia onboarding" in authenticated_response.content
    assert reverse("onboarding-index").encode() in authenticated_response.content
    assert reverse("account_logout").encode() in authenticated_response.content
    assert b"X-CSRFToken" in authenticated_response.content


@override_settings(ROOT_URLCONF="config.urls")
def test_logout_keeps_public_redirect_and_clears_workspace_cookie(client):
    client.cookies[WORKSPACE_COOKIE] = "test.zeus.cais.uno"

    response = client.get(reverse("account_logout"))

    assert response.status_code == 302
    assert response.url == "https://zeus.cais.uno/accounts/login/"
    assert response.cookies[WORKSPACE_COOKIE]["max-age"] == 0


@override_settings(ROOT_URLCONF="config.urls")
def test_login_post_rejects_missing_csrf_token():
    request = RequestFactory().post(
        reverse("account_login"),
        {"login": "ui-baseline@example.com", "password": "test-password"},
    )
    middleware = CsrfViewMiddleware(lambda current_request: None)

    response = middleware.process_view(request, public_login, (), {})

    assert response.status_code == 403
