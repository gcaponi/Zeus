import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

from apps.core.admin import ClientAdmin
from apps.core.models import Client

pytestmark = pytest.mark.django_db


@pytest.fixture
def client_admin():
    return ClientAdmin(Client, AdminSite())


@pytest.fixture
def limited_staff():
    return get_user_model().objects.create_user(
        username="limited-admin",
        email="limited-admin@example.com",
        password="pw",
        is_staff=True,
    )


@pytest.mark.parametrize("method", ["get", "post"])
def test_staff_cannot_open_or_submit_tenant_password_reset(
    client_admin,
    limited_staff,
    method,
):
    request = getattr(RequestFactory(), method)(
        "/admin/core/client/1/change-password/",
        data={"new_password": "A-strong-password-2026", "confirm_password": "A-strong-password-2026"},
    )
    request.user = limited_staff

    with pytest.raises(PermissionDenied):
        client_admin.change_password_view(request, client_id=1)


def test_password_reset_link_is_hidden_without_permission(client_admin, limited_staff):
    request = RequestFactory().get("/admin/core/client/")
    request.user = limited_staff

    assert "change_password_link" not in client_admin.get_list_display(request)


def test_superuser_sees_password_reset_link(client_admin):
    superuser = get_user_model().objects.create_superuser(
        username="root",
        email="root@example.com",
        password="pw",
    )
    request = RequestFactory().get("/admin/core/client/")
    request.user = superuser

    assert "change_password_link" in client_admin.get_list_display(request)
