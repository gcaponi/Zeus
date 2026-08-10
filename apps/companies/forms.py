import re

from django import forms
from django.core.validators import URLValidator
from django.forms import formset_factory

from apps.companies.models import Company


INPUT_CLASS = "zeus-input"


class CompanyAnagraficaForm(forms.ModelForm):
    sito_web = forms.CharField(
        label="Sito web",
        widget=forms.URLInput(attrs={"class": INPUT_CLASS, "placeholder": "https://azienda.it"}),
    )

    class Meta:
        model = Company
        fields = [
            "name",
            "nome_commerciale",
            "partita_iva",
            "codice_fiscale",
            "rea",
            "pec",
            "email_contatto",
            "sito_web",
        ]
        labels = {
            "name": "Ragione sociale",
            "nome_commerciale": "Nome commerciale",
            "partita_iva": "Partita IVA",
            "codice_fiscale": "Codice Fiscale",
            "rea": "REA",
            "pec": "PEC",
            "email_contatto": "Email",
        }
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "nome_commerciale": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "partita_iva": forms.TextInput(
                attrs={"class": INPUT_CLASS, "inputmode": "numeric", "maxlength": "11"}
            ),
            "codice_fiscale": forms.TextInput(
                attrs={"class": INPUT_CLASS, "autocomplete": "off", "maxlength": "16"}
            ),
            "rea": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Es. MI-1234567"}),
            "pec": forms.EmailInput(attrs={"class": INPUT_CLASS}),
            "email_contatto": forms.EmailInput(attrs={"class": INPUT_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = True

    def clean_partita_iva(self):
        value = re.sub(r"\s+", "", self.cleaned_data["partita_iva"])
        if not re.fullmatch(r"\d{11}", value):
            raise forms.ValidationError("Inserisci una Partita IVA di 11 cifre.")
        return value

    def clean_codice_fiscale(self):
        value = re.sub(r"\s+", "", self.cleaned_data["codice_fiscale"]).upper()
        if not re.fullmatch(r"[A-Z0-9]{11,16}", value):
            raise forms.ValidationError(
                "Usa da 11 a 16 caratteri alfanumerici, senza spazi."
            )
        return value

    def clean_rea(self):
        return self.cleaned_data["rea"].strip().upper()

    def clean_sito_web(self):
        value = self.cleaned_data["sito_web"].strip()
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
            value = f"https://{value}"
        URLValidator(schemes=["http", "https"])(value)
        return value


class ContactValueForm(forms.Form):
    value = forms.CharField(
        label="Numero",
        max_length=40,
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "inputmode": "tel",
                "placeholder": "+39 02 1234567",
            }
        ),
    )

    def clean_value(self):
        value = " ".join(self.cleaned_data["value"].split())
        if not re.fullmatch(r"[+()\d\s.-]+", value) or len(re.sub(r"\D", "", value)) < 6:
            raise forms.ValidationError("Inserisci un numero di telefono valido.")
        return value


class SocialProfileForm(forms.Form):
    network = forms.CharField(
        label="Social",
        max_length=80,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "LinkedIn"}),
    )
    url = forms.URLField(
        label="URL profilo",
        max_length=2048,
        assume_scheme="https",
        widget=forms.URLInput(
            attrs={"class": INPUT_CLASS, "placeholder": "https://linkedin.com/company/..."}
        ),
    )


PhoneFormSet = formset_factory(
    ContactValueForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
)
WhatsAppFormSet = formset_factory(
    ContactValueForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
)
SocialFormSet = formset_factory(
    SocialProfileForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
)
