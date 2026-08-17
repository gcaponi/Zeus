from allauth.account.forms import SignupForm
from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.text import slugify
from django_tenants.utils import schema_context

from apps.core.models import Client, SignupProvisioning, WorkspaceAccess


def _slug_reserved_by_other(slug, email):
    """True se lo slug e' un tenant attivo o e' riservato da un provisioning
    valido di un altro utente; lo stesso email puo' riusare il proprio pending."""
    if Client.objects.filter(schema_name__iexact=slug).exists():
        return True
    now = timezone.now()
    reserved = SignupProvisioning.objects.filter(slug__iexact=slug).exclude(
        status=SignupProvisioning.STATUS_FAILED,
    )
    for row in reserved:
        if row.status == SignupProvisioning.STATUS_COMPLETED:
            return True
        if row.status == SignupProvisioning.STATUS_PENDING:
            if row.expires_at is not None and row.expires_at < now:
                continue
            if email and row.email.lower() == email.lower():
                continue
            return True
    return False


def _workspace_slug_from_name(company_name, email):
    """Slug workspace unico generato dal nome azienda.

    Minuscolo e sicuro sia per il sottodominio DNS sia per lo schema
    Postgres (slugify). Se il nome e' gia' occupato da un altro workspace
    aggiunge un suffisso numerico (acme, acme-2, acme-3, ...)."""
    base = slugify(company_name)[:50].strip("-") or "workspace"
    slug = base
    suffix = 2
    while _slug_reserved_by_other(slug, email):
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


class ZEUSSignupForm(SignupForm):
    company_name = forms.CharField(
        max_length=100,
        label="Company name",
    )

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get("email")
        company_name = cleaned_data.get("company_name")
        if not email or not company_name:
            return cleaned_data

        slug = _workspace_slug_from_name(company_name, email)
        cleaned_data["company_slug"] = slug

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
