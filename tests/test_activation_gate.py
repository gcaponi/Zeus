"""Attivazione specialista solo con DNA approvato (finding #18).

product_promote guardava solo lo status IN_VALIDAZIONE. L'agent includeva
il DNA completo corrente di ogni prodotto attivo, anche non approvato.
Promozione e prompt devono richiedere lo stesso invariante.
"""
import pytest
from django.urls import reverse
from django.utils import timezone

from apps.companies import agent as agent_service
from apps.companies import views
from apps.companies.models import Company, DNAGenerale, ProductDNA, Specialista

pytestmark = pytest.mark.django_db


def _company():
    company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
    DNAGenerale.objects.create(
        company=company,
        version=1,
        dna_type=DNAGenerale.TYPE_COMPLETE,
        content={"sintesi_cognitiva": "DNA generale approvato."},
        is_approved=timezone.now(),
    )
    return company


def _product(company, *, status=Specialista.STATUS_IN_VALIDAZIONE, approved=False, dna_type=None):
    product = Specialista.objects.create(
        company=company,
        name="Vasca",
        slug="vasca",
        codice="VB-001",
        status=status,
    )
    ProductDNA.objects.create(
        product=product,
        version=1,
        dna_type=dna_type or ProductDNA.TYPE_COMPLETE,
        content={"sintesi_cognitiva": "Scheda tecnica vasca non revisionata."},
        is_approved=timezone.now() if approved else None,
    )
    return product


def _promote(rf_with_tenant, product):
    return views.product_promote(
        rf_with_tenant(
            "post",
            reverse("specialista-promote", args=[product.pk]),
            form=True,
        ),
        product.pk,
    )


def test_promote_fails_without_complete_approved_dna(rf_with_tenant):
    company = _company()
    product = _product(company, approved=False)

    response = _promote(rf_with_tenant, product)

    product.refresh_from_db()
    assert response.status_code == 400
    assert b"approvato" in response.content
    assert product.status == Specialista.STATUS_IN_VALIDAZIONE


def test_promote_fails_when_only_pre_dna_is_approved(rf_with_tenant):
    company = _company()
    product = _product(
        company,
        approved=True,
        dna_type=ProductDNA.TYPE_PRE,
    )

    response = _promote(rf_with_tenant, product)

    product.refresh_from_db()
    assert response.status_code == 400
    assert product.status == Specialista.STATUS_IN_VALIDAZIONE


def test_promote_succeeds_with_approved_complete_dna(rf_with_tenant):
    company = _company()
    product = _product(company, approved=True)

    response = _promote(rf_with_tenant, product)

    product.refresh_from_db()
    assert response.status_code == 302
    assert response["Location"] == reverse("specialista-detail", args=[product.pk])
    assert product.status == Specialista.STATUS_ATTIVO


def test_agent_excludes_active_product_without_approved_dna():
    company = _company()
    _product(
        company,
        status=Specialista.STATUS_ATTIVO,
        approved=False,
    )
    approved = Specialista.objects.create(
        company=company,
        name="Banco bar",
        slug="banco-bar",
        codice="BB-001",
        status=Specialista.STATUS_ATTIVO,
    )
    ProductDNA.objects.create(
        product=approved,
        version=1,
        dna_type=ProductDNA.TYPE_COMPLETE,
        content={"sintesi_cognitiva": "Specialista banco bar approvato."},
        is_approved=timezone.now(),
    )

    prompt = agent_service.build_system_prompt(company)

    assert prompt is not None
    assert "DNA Specialista — Banco bar" in prompt
    assert "Specialista banco bar approvato" in prompt
    assert "DNA Specialista — Vasca" not in prompt
    assert "non revisionata" not in prompt
