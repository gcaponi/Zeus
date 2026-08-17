"""Limiti upload prima del parse (finding #15).

I path company e product chiamavano _extract_company_file_text dopo il solo
controllo di sospensione. Il helper materializzava l'intero body e camminava
tutte le pagine PDF (piu' OCR). Oversized e troppe pagine devono essere
rifiutati prima di read() o del parser.
"""
from unittest.mock import Mock

import fitz
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from apps.companies import views
from apps.companies.models import Company, CompanyFile, ProductFile, Specialista

pytestmark = pytest.mark.django_db


class _OversizedUpload:
    name = "huge.pdf"
    size = 20 * 1024 * 1024

    def read(self, *args, **kwargs):
        raise AssertionError("oversized body must not be read")

    def seek(self, *args, **kwargs):
        raise AssertionError("oversized body must not be seeked")


class _UnknownSizeUpload:
    name = "mystery.txt"
    size = None

    def read(self, *args, **kwargs):
        raise AssertionError("unknown-size body must not be read")

    def seek(self, *args, **kwargs):
        raise AssertionError("unknown-size body must not be seeked")


def _company():
    return Company.objects.create(schema_name="test-tenant", name="Test Tenant")


def _pdf_bytes(pages, text="pagina"):
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"{text} {index + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def _patch_extract(monkeypatch):
    extract = Mock(side_effect=AssertionError("parser must not run"))
    monkeypatch.setattr(views, "_extract_company_file_text", extract)
    return extract


def test_admit_rejects_oversized_before_read():
    reason = views._admit_uploaded_file(_OversizedUpload())

    assert reason is not None
    assert "supera il limite" in reason


def test_admit_rejects_unknown_size_before_read():
    reason = views._admit_uploaded_file(_UnknownSizeUpload())

    assert reason is not None
    assert "dimensione" in reason


def test_admit_rejects_pdf_extension_without_magic():
    uploaded = SimpleUploadedFile(
        "fake.pdf",
        b"questo non e un pdf",
        content_type="application/pdf",
    )

    reason = views._admit_uploaded_file(uploaded)

    assert reason == "Il file non è un PDF valido."


@override_settings(COMPANY_FILE_MAX_PDF_PAGES=2)
def test_admit_rejects_excessive_pages_without_text_extraction(monkeypatch):
    page_text = Mock(side_effect=AssertionError("page.get_text must not run"))
    monkeypatch.setattr(fitz.Page, "get_text", page_text)
    uploaded = SimpleUploadedFile(
        "many.pdf",
        _pdf_bytes(3),
        content_type="application/pdf",
    )

    reason = views._admit_uploaded_file(uploaded)

    assert reason == "Il PDF supera il limite di 2 pagine."
    page_text.assert_not_called()


@override_settings(COMPANY_FILE_MAX_PDF_PAGES=2)
def test_admit_accepts_pdf_within_page_limit():
    uploaded = SimpleUploadedFile(
        "ok.pdf",
        _pdf_bytes(2),
        content_type="application/pdf",
    )

    assert views._admit_uploaded_file(uploaded) is None


def test_onboarding_upload_rejects_oversized_without_extract(rf_with_tenant, monkeypatch):
    extract = _patch_extract(monkeypatch)
    company = _company()
    request = rf_with_tenant("post", reverse("onboarding-file-upload"), {}, form=True)
    request.FILES["company_file"] = _OversizedUpload()

    response = views.onboarding_file_upload(request)

    extract.assert_not_called()
    assert response.status_code == 200
    assert b"supera il limite" in response.content
    assert not CompanyFile.objects.filter(company=company).exists()


@override_settings(COMPANY_FILE_MAX_PDF_PAGES=2)
def test_onboarding_upload_rejects_excessive_pages_without_extract(
    rf_with_tenant, monkeypatch,
):
    extract = _patch_extract(monkeypatch)
    company = _company()
    request = rf_with_tenant(
        "post",
        reverse("onboarding-file-upload"),
        {
            "company_file": SimpleUploadedFile(
                "many.pdf",
                _pdf_bytes(3),
                content_type="application/pdf",
            ),
        },
        form=True,
    )

    response = views.onboarding_file_upload(request)

    extract.assert_not_called()
    assert b"supera il limite di 2 pagine" in response.content
    assert not CompanyFile.objects.filter(company=company).exists()


def test_save_company_file_rejects_oversized_without_extract(rf_with_tenant, monkeypatch):
    extract = _patch_extract(monkeypatch)
    company = _company()
    request = rf_with_tenant("post", "/onboarding/source/", {}, form=True)
    request.FILES["company_file"] = _OversizedUpload()

    reason = views._save_company_file_from_request(company, request)

    extract.assert_not_called()
    assert reason is not None
    assert "supera il limite" in reason
    assert not CompanyFile.objects.filter(company=company).exists()


def test_product_upload_rejects_oversized_without_extract(rf_with_tenant, monkeypatch):
    extract = _patch_extract(monkeypatch)
    company = _company()
    product = Specialista.objects.create(company=company, name="Vasca", slug="vasca")
    request = rf_with_tenant(
        "post",
        reverse("specialista-file-upload", args=[product.pk]),
        {},
        form=True,
    )
    request.FILES["file"] = _OversizedUpload()
    request.META["HTTP_ACCEPT"] = "application/json"

    response = views.product_file_upload(request, product.pk)

    extract.assert_not_called()
    assert response.status_code == 400
    assert b"supera il limite" in response.content
    assert not ProductFile.objects.filter(product=product).exists()


@override_settings(COMPANY_FILE_MAX_PDF_PAGES=2)
def test_product_upload_rejects_excessive_pages_without_extract(
    rf_with_tenant, monkeypatch,
):
    extract = _patch_extract(monkeypatch)
    company = _company()
    product = Specialista.objects.create(company=company, name="Vasca", slug="vasca")
    request = rf_with_tenant(
        "post",
        reverse("specialista-file-upload", args=[product.pk]),
        {
            "file": SimpleUploadedFile(
                "many.pdf",
                _pdf_bytes(3),
                content_type="application/pdf",
            ),
        },
        form=True,
    )
    request.META["HTTP_ACCEPT"] = "application/json"

    response = views.product_file_upload(request, product.pk)

    extract.assert_not_called()
    assert response.status_code == 400
    assert b"supera il limite di 2 pagine" in response.content
    assert not ProductFile.objects.filter(product=product).exists()
