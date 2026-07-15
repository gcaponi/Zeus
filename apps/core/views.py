from allauth.account.utils import perform_login
from allauth.account.views import SignupView
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django_tenants.utils import schema_context

from apps.core.forms import ZEUSSignupForm
from apps.core.models import Client, Domain, Plan, WorkspaceAccess, WorkspaceSubscription

WORKSPACE_COOKIE = "zeus_workspace"
WORKSPACE_COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def health_check(request):
    return JsonResponse({"status": "ok"})


def app_shell_preview(request):
    if not settings.ZEUS_APP_SHELL_ENABLED:
        raise Http404
    return render(request, "core/app_shell_preview.html")


def _set_workspace_cookie(response, workspace):
    response.set_cookie(
        WORKSPACE_COOKIE,
        workspace,
        max_age=WORKSPACE_COOKIE_MAX_AGE,
        samesite="Lax",
    )
    return response


def _clear_workspace_cookie(response):
    response.delete_cookie(WORKSPACE_COOKIE)
    response.delete_cookie(WORKSPACE_COOKIE, domain=".zeus.cais.uno")
    return response


def _valid_workspace_cookie(request):
    workspace = request.COOKIES.get(WORKSPACE_COOKIE, "").strip().lower()
    if not workspace:
        return None
    if "/" in workspace or not workspace.endswith(".zeus.cais.uno"):
        return None
    if not Domain.objects.filter(domain=workspace).exists():
        return None
    return workspace


def redirect_to_workspace_or_login(request):
    workspace = _valid_workspace_cookie(request)
    if workspace:
        return redirect(f"https://{workspace}/onboarding/")
    response = redirect("https://zeus.cais.uno/accounts/login/")
    return _clear_workspace_cookie(response)


class ZEUSSignupView(SignupView):
    form_class = ZEUSSignupForm
    template_name = "account/signup.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            auth_logout(request)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        slug = form.cleaned_data["company_slug"]
        email = form.cleaned_data["email"]

        tenant = Client(schema_name=slug, name=form.cleaned_data["company_name"])
        tenant.save()

        domain = Domain.objects.create(
            domain=f"{slug}.zeus.cais.uno",
            tenant=tenant,
            is_primary=True,
        )

        # Crea mappa email→workspace nel public schema
        WorkspaceAccess.objects.create(
            email=email,
            tenant_domain=domain.domain,
        )

        WorkspaceSubscription.objects.create(
            client=tenant,
            plan=Plan.get_default(),
        )

        with schema_context(slug):
            user = form.save(self.request)
            perform_login(self.request, user, email_verification=False)

        response = redirect(f"https://{domain.domain}/onboarding/")
        return _set_workspace_cookie(response, domain.domain)


def public_login(request):
    """Login su zeus.cais.uno che trova workspace e redirect alla dashboard."""
    if request.method == "POST":
        email = request.POST.get("login", "").strip()
        password = request.POST.get("password", "")
        access = WorkspaceAccess.objects.filter(email__iexact=email).first()
        if not access:
            return render(request, "account/login.html", {
                "error": "Email o password non validi.",
            })

        tenant_schema = access.tenant_domain.split(".zeus.cais.uno", 1)[0]
        with schema_context(tenant_schema):
            user = authenticate(request, username=email, password=password)

            if user is None:
                error = True  # usciamo per renderizzare fuori
            else:
                auth_login(request, user)
                error = False

        if error or user is None:
            return render(request, "account/login.html", {
                "error": "Email o password non validi.",
            })

        response = redirect(
            f"https://{access.tenant_domain}{settings.LOGIN_REDIRECT_URL}"
        )
        return _set_workspace_cookie(response, access.tenant_domain)

    return render(request, "account/login.html", {"error": None})


def tenant_landing(request):
    tenant = request.tenant if hasattr(request, "tenant") else None
    is_public = tenant is None or tenant.schema_name == "public"
    if is_public and request.user.is_authenticated:
        workspace = _valid_workspace_cookie(request)
        if workspace:
            return redirect(f"https://{workspace}/onboarding/")
    return render(request, "core/tenant_landing.html", {
        "tenant": tenant,
        "is_public": is_public,
    })


def public_onboarding_redirect(request):
    """Redirect intelligente da zeus.cais.uno/onboarding/ al workspace corretto."""
    return redirect_to_workspace_or_login(request)


@login_required
def tenant_dashboard(request):
    tenant = request.tenant if hasattr(request, "tenant") else None
    template_name = (
        "core/app_shell_dashboard.html"
        if settings.ZEUS_APP_SHELL_ENABLED
        else "core/tenant_dashboard.html"
    )
    return render(request, template_name, {
        "tenant": tenant,
        "user": request.user,
    })


def public_logout(request):
    """Logout from any domain and redirect to public login with cookie cleared."""
    auth_logout(request)
    response = redirect("https://zeus.cais.uno/accounts/login/")
    return _clear_workspace_cookie(response)
