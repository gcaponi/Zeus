from allauth.account.forms import SignupForm
from django import forms


class ZEUSSignupForm(SignupForm):
    company_name = forms.CharField(
        max_length=100,
        label="Company name",
    )
    company_slug = forms.SlugField(
        max_length=63,
        label="Company slug",
        help_text="Showname used for your subdomain: {slug}.zeus.cais.uno",
    )
