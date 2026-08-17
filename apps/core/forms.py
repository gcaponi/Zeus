from allauth.account.forms import SignupForm
from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.core.models import Client, SignupProvisioning, WorkspaceAccess

_SLUG_TAKEN = (
    'The workspace URL "{slug}.zeus.cais.uno" is already taken. Please choose another.'
)


class ZEUSSignupForm(SignupForm):
    company_name = forms.CharField(
        max_length=100,
        label="Company name",
    )
    company_slug = forms.SlugField(
        max_length=63,
        label="Company slug",
        help_text="Used for your subdomain: {slug}.zeus.cais.uno",
    )

    def clean_company_slug(self):
        # HTTP Host e' case-insensitive: uno slug "Testes" crea
        # Testes.zeus.cais.uno, il browser chiede testes.zeus.cais.uno, 404.
        slug = self.cleaned_data["company_slug"].lower()
        if Client.objects.filter(schema_name__iexact=slug).exists():
            raise forms.ValidationError(_SLUG_TAKEN.format(slug=slug))
        now = timezone.now()
        reserved = SignupProvisioning.objects.filter(slug__iexact=slug).exclude(
            status=SignupProvisioning.STATUS_FAILED,
        )
        email = (self.data.get("email") or "").strip()
        for row in reserved:
            if row.status == SignupProvisioning.STATUS_COMPLETED:
                raise forms.ValidationError(_SLUG_TAKEN.format(slug=slug))
            if row.status == SignupProvisioning.STATUS_PENDING:
                if row.expires_at is not None and row.expires_at < now:
                    continue
                if email and row.email.lower() == email.lower():
                    continue
                raise forms.ValidationError(_SLUG_TAKEN.format(slug=slug))
        return slug

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get("email")
        slug = cleaned_data.get("company_slug")
        if not email or not slug:
            return cleaned_data

        if WorkspaceAccess.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                {"email": "This email is already registered in another workspace."}
            )

        user_model = get_user_model()
        for tenant in Client.objects.exclude(schema_name=slug):
            with schema_context(tenant.schema_name):
                if user_model.objects.filter(email__iexact=email).exists():
                    raise forms.ValidationError(
                        {"email": "This email is already registered in another workspace."}
                    )
        return cleaned_data
