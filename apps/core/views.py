from django.http import JsonResponse
from django.shortcuts import redirect, render
from allauth.account.utils import perform_login
from allauth.account.views import SignupView
from django_tenants.utils import schema_context

from apps.core.forms import ZEUSSignupForm
from apps.core.models import Client, Domain


def health_check(request):
    return JsonResponse({"status": "ok"})


class ZEUSSignupView(SignupView):
    form_class = ZEUSSignupForm
    template_name = "account/signup.html"

    def form_valid(self, form):
        slug = form.cleaned_data["company_slug"]

        tenant = Client(schema_name=slug, name=form.cleaned_data["company_name"])
        tenant.save()

        Domain.objects.create(
            domain=f"{slug}.zeus.cais.uno",
            tenant=tenant,
            is_primary=True,
        )

        with schema_context(slug):
            user = form.save(self.request)
            perform_login(self.request, user, email_verification=False)

        return redirect("https://zeus.cais.uno/admin/")


def tenant_landing(request):
    tenant = request.tenant if hasattr(request, "tenant") else None
    is_public = tenant is None or tenant.schema_name == "public"
    return render(request, "core/tenant_landing.html", {
        "tenant": tenant,
        "is_public": is_public,
    })


from django.contrib.auth.decorators import login_required


@login_required
def tenant_dashboard(request):
    tenant = request.tenant if hasattr(request, "tenant") else None
    return render(request, "core/tenant_dashboard.html", {
        "tenant": tenant,
        "user": request.user,
    })
