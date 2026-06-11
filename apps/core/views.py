from allauth.account.utils import perform_login
from allauth.account.views import SignupView
from django.contrib.auth import authenticate
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django_tenants.utils import schema_context

from apps.core.forms import ZEUSSignupForm
from apps.core.models import Client, Domain, WorkspaceAccess

WORKSPACE_COOKIE = "zeus_workspace"
WORKSPACE_COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def health_check(request):
    return JsonResponse({"status": "ok"})


class ZEUSSignupView(SignupView):
    form_class = ZEUSSignupForm
    template_name = "account/signup.html"

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

        with schema_context(slug):
            user = form.save(self.request)
            perform_login(self.request, user, email_verification=False)

        response = redirect(f"https://{domain.domain}/onboarding/")
        response.set_cookie(
            WORKSPACE_COOKIE,
            domain.domain,
            max_age=WORKSPACE_COOKIE_MAX_AGE,
            samesite="Lax",
        )
        return response


def public_login(request):
    """Login su zeus.cais.uno che trova workspace e redirect a onboarding."""
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
            return render(request, "account/login.html", {
                "error": "Email o password non validi.",
            })

        auth_login(request, user)

        response = redirect(f"https://{access.tenant_domain}/onboarding/")
        response.set_cookie(
            WORKSPACE_COOKIE,
            access.tenant_domain,
            max_age=WORKSPACE_COOKIE_MAX_AGE,
            samesite="Lax",
        )
        return response

    return render(request, "account/login.html", {"error": None})


def tenant_landing(request):
    tenant = request.tenant if hasattr(request, "tenant") else None
    is_public = tenant is None or tenant.schema_name == "public"
    if is_public and request.user.is_authenticated:
        workspace = request.COOKIES.get(WORKSPACE_COOKIE)
        if workspace:
            return redirect(f"https://{workspace}/onboarding/")
    return render(request, "core/tenant_landing.html", {
        "tenant": tenant,
        "is_public": is_public,
    })


@login_required
def tenant_dashboard(request):
    tenant = request.tenant if hasattr(request, "tenant") else None
    return render(request, "core/tenant_dashboard.html", {
        "tenant": tenant,
        "user": request.user,
    })


def public_logout(request):
    """Logout from any domain and redirect to public login with cookie cleared."""
    auth_logout(request)
    response = redirect("https://zeus.cais.uno/accounts/login/")
    response.delete_cookie(WORKSPACE_COOKIE)
    return response
