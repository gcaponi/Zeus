"""Shared pytest fixtures for the ZEUS test suite."""
import pytest


@pytest.fixture
def rf_with_tenant(django_user_model):
    """RequestFactory with request.tenant + authenticated user.

    Mirrors how the tenant middleware attaches the current tenant to the
    request. Shared across test modules so views can be exercised without
    the full HTTP client + tenant routing stack.
    """
    from django.test.client import RequestFactory

    rf = RequestFactory()
    user = django_user_model.objects.create_user(username="u", email="test@x.it", password="pw")

    def _make(method, path, data=None, form=False):
        if method == "post":
            if form:
                req = rf.post(path, data or {})
            else:
                req = rf.post(path, __import__("json").dumps(data or {}), content_type="application/json")
        else:
            req = rf.get(path)

        class FakeTenant:
            schema_name = "test-tenant"
            name = "Test Tenant"

        req.tenant = FakeTenant()
        req.user = user
        return req

    return _make
