from allauth.account.forms import SignupForm
from django import forms

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
