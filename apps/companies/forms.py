import re

from django import forms
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from django.forms import formset_factory

from apps.companies.models import Company


INPUT_CLASS = "zeus-input"


def guided_input(example, mask, **attrs):
    """Return consistent, user-visible format guidance for an input."""
    return {
        "class": INPUT_CLASS,
        "placeholder": f"Es. {example}",
        "title": f"Esempio corretto: {example}",
        "data-input-mask": mask,
        "data-format-example": example,
        **attrs,
    }


class CompanyAnagraficaForm(forms.ModelForm):
    sito_web = forms.CharField(
        label="Sito web",
        error_messages={
            "required": "Il sito web è obbligatorio. Esempio: https://azienda.it.",
        },
        widget=forms.URLInput(
            attrs=guided_input(
                "https://azienda.it",
                "url",
                autocomplete="url",
                inputmode="url",
            )
        ),
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
        error_messages = {
            "name": {
                "required": "La ragione sociale è obbligatoria. Esempio: Cais S.r.l.",
                "max_length": "La ragione sociale è troppo lunga. Esempio: Cais S.r.l.",
            },
            "nome_commerciale": {
                "required": "Il nome commerciale è obbligatorio. Esempio: Cais.",
                "max_length": "Il nome commerciale è troppo lungo. Esempio: Cais.",
            },
            "partita_iva": {
                "required": "La Partita IVA è obbligatoria. Esempio: 01234567890.",
                "max_length": "La Partita IVA deve avere 11 cifre. Esempio: 01234567890.",
            },
            "codice_fiscale": {
                "required": "Il Codice Fiscale è obbligatorio. Esempio: RSSMRA80A01H501U.",
                "max_length": "Il Codice Fiscale può avere al massimo 16 caratteri. Esempio: RSSMRA80A01H501U.",
            },
            "rea": {
                "required": "Il REA è obbligatorio. Esempio: MI-1234567.",
                "max_length": "Il REA è troppo lungo. Esempio: MI-1234567.",
            },
            "pec": {
                "required": "La PEC è obbligatoria. Esempio: azienda@pec.it.",
                "invalid": "Inserisci una PEC valida. Esempio: azienda@pec.it.",
            },
            "email_contatto": {
                "required": "L’email è obbligatoria. Esempio: info@azienda.it.",
                "invalid": "Inserisci un’email valida. Esempio: info@azienda.it.",
            },
        }
        widgets = {
            "name": forms.TextInput(
                attrs=guided_input("Cais S.r.l.", "text", autocomplete="organization")
            ),
            "nome_commerciale": forms.TextInput(
                attrs=guided_input("Cais", "text", autocomplete="organization-title")
            ),
            "partita_iva": forms.TextInput(
                attrs=guided_input(
                    "01234567890",
                    "digits",
                    inputmode="numeric",
                    maxlength="11",
                    pattern="[0-9]{11}",
                    autocomplete="off",
                )
            ),
            "codice_fiscale": forms.TextInput(
                attrs=guided_input(
                    "RSSMRA80A01H501U",
                    "alphanumeric-upper",
                    autocomplete="off",
                    maxlength="16",
                    pattern="[A-Za-z0-9]{11,16}",
                )
            ),
            "rea": forms.TextInput(
                attrs=guided_input(
                    "MI-1234567",
                    "rea",
                    autocomplete="off",
                    pattern="[A-Za-z]{2}-?[0-9]{1,10}",
                )
            ),
            "pec": forms.EmailInput(
                attrs=guided_input(
                    "azienda@pec.it",
                    "email",
                    autocomplete="email",
                    inputmode="email",
                )
            ),
            "email_contatto": forms.EmailInput(
                attrs=guided_input(
                    "info@azienda.it",
                    "email",
                    autocomplete="email",
                    inputmode="email",
                )
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = True

    def clean_partita_iva(self):
        value = re.sub(r"\s+", "", self.cleaned_data["partita_iva"])
        if not re.fullmatch(r"\d{11}", value):
            raise forms.ValidationError(
                "Inserisci una Partita IVA di 11 cifre. Esempio: 01234567890."
            )
        return value

    def clean_codice_fiscale(self):
        value = re.sub(r"\s+", "", self.cleaned_data["codice_fiscale"]).upper()
        if not re.fullmatch(r"[A-Z0-9]{11,16}", value):
            raise forms.ValidationError(
                "Usa da 11 a 16 caratteri alfanumerici, senza spazi. "
                "Esempio: RSSMRA80A01H501U."
            )
        return value

    def clean_rea(self):
        value = re.sub(r"\s+", "", self.cleaned_data["rea"]).upper()
        match = re.fullmatch(r"([A-Z]{2})-?(\d{1,10})", value)
        if not match:
            raise forms.ValidationError(
                "Inserisci provincia e numero REA. Esempio: MI-1234567."
            )
        return f"{match.group(1)}-{match.group(2)}"

    def clean_sito_web(self):
        value = self.cleaned_data["sito_web"].strip()
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
            value = f"https://{value}"
        try:
            URLValidator(schemes=["http", "https"])(value)
        except DjangoValidationError as exc:
            raise forms.ValidationError(
                "Inserisci un sito web valido. Esempio: https://azienda.it."
            ) from exc
        return value


class ContactValueForm(forms.Form):
    value = forms.CharField(
        label="Numero",
        max_length=40,
        error_messages={
            "required": "Il numero è obbligatorio. Esempio: +39 333 1234567.",
            "max_length": "Il numero è troppo lungo. Esempio: +39 333 1234567.",
        },
        widget=forms.TextInput(
            attrs=guided_input(
                "+39 333 1234567",
                "phone",
                inputmode="tel",
                autocomplete="tel",
                pattern="[+()0-9 .-]{6,40}",
            )
        ),
    )

    def clean_value(self):
        value = " ".join(self.cleaned_data["value"].split())
        if not re.fullmatch(r"[+()\d\s.-]+", value) or len(re.sub(r"\D", "", value)) < 6:
            raise forms.ValidationError(
                "Inserisci un numero valido. Esempio: +39 333 1234567."
            )
        return value


class SocialProfileForm(forms.Form):
    network = forms.CharField(
        label="Social",
        max_length=80,
        error_messages={
            "required": "Il nome del social è obbligatorio. Esempio: LinkedIn.",
            "max_length": "Il nome del social è troppo lungo. Esempio: LinkedIn.",
        },
        widget=forms.TextInput(
            attrs=guided_input("LinkedIn", "text", autocomplete="off")
        ),
    )
    url = forms.URLField(
        label="URL profilo",
        max_length=2048,
        assume_scheme="https",
        error_messages={
            "required": "Il link del profilo è obbligatorio. Esempio: https://linkedin.com/company/azienda.",
            "invalid": "Inserisci un link social valido. Esempio: https://linkedin.com/company/azienda.",
            "max_length": "Il link social è troppo lungo. Esempio: https://linkedin.com/company/azienda.",
        },
        widget=forms.URLInput(
            attrs=guided_input(
                "https://linkedin.com/company/azienda",
                "url",
                autocomplete="url",
                inputmode="url",
            )
        ),
    )


PhoneFormSet = formset_factory(
    ContactValueForm,
    extra=0,
    can_delete=True,
    min_num=1,
    validate_min=True,
)
WhatsAppFormSet = formset_factory(
    ContactValueForm,
    extra=0,
    can_delete=True,
    min_num=1,
    validate_min=True,
)
SocialFormSet = formset_factory(
    SocialProfileForm,
    extra=0,
    can_delete=True,
    min_num=1,
    validate_min=True,
)
