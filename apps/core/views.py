from allauth.account.utils import perform_login
from allauth.account.views import SignupView
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
    """Login su zeus.cais.uno che redirect al tenant workspace."""
    if request.method == "POST":
        email = request.POST.get("login", "").strip()

        # Cerca workspace nel public schema
        try:
            access = WorkspaceAccess.objects.get(email__iexact=email)
            # Redirect al tenant subdomain con email (login avviene nel tenant)
            redirect_url = f"https://{access.tenant_domain}/accounts/login/?email={email}"
            return redirect(redirect_url)
        except WorkspaceAccess.DoesNotExist:
            # Email non registrata
            return render(request, "account/login.html", {
                "form": None,
                "error": "Email non registrata. Crea prima un account.",
            })

    return render(request, "account/login.html", {"form": None, "error": None})


def tenant_landing(request):
    tenant = request.tenant if hasattr(request, "tenant") else None
    is_public = tenant is None or tenant.schema_name == "public"
    if is_public:
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
