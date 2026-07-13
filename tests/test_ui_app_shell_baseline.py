from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.middleware.csrf import CsrfViewMiddleware
from django.test import RequestFactory, override_settings
from django.urls import resolve, reverse

from apps.core.views import (
    WORKSPACE_COOKIE,
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
@override_settings(ROOT_URLCONF="config.urls")
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
