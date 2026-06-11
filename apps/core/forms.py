from allauth.account.forms import SignupForm
from django import forms
from django.contrib.auth import get_user_model
from django_tenants.utils import schema_context

from apps.core.models import Client


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
        slug = self.cleaned_data["company_slug"]
        if Client.objects.filter(schema_name=slug).exists():
            raise forms.ValidationError(
                f'The workspace URL "{slug}.zeus.cais.uno" is already taken. Please choose another.'
            )
        return slug

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get("email")
        slug = cleaned_data.get("company_slug")
        if not email or not slug:
            return cleaned_data

        user_model = get_user_model()
        for tenant in Client.objects.exclude(schema_name=slug):
            with schema_context(tenant.schema_name):
                if user_model.objects.filter(email__iexact=email).exists():
                    raise forms.ValidationError(
                        {"email": "This email is already registered in another workspace."}
                    )
        return cleaned_data
