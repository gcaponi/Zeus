from functools import partial
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.companies import tasks, views
from apps.companies.models import (
    Company,
    CompanyQuestion,
    ConsistencyIssue,
    DNAGenerale,
    PaidOperation,
    PipelineRun,
    ProductDNA,
    ProductFile,
    ProductQuestion,
    Source,
    Specialista,
)
from apps.core.models import Client, Plan, WorkspaceSubscription


pytestmark = pytest.mark.django_db


@pytest.fixture
def paid_workspace(rf_with_tenant, monkeypatch):
    monkeypatch.setattr(Client, "auto_create_schema", False)
    tenant = Client.objects.create(schema_name="test-tenant", name="Test Tenant")
    subscription = WorkspaceSubscription.objects.create(
        client=tenant,
        plan=Plan.get_default(),
        status=WorkspaceSubscription.STATUS_ACTIVE,
    )
    company = Company.objects.create(schema_name="test-tenant", name="Test Tenant")
    return SimpleNamespace(
        company=company,
        request=rf_with_tenant,
        subscription=subscription,
    )


def _company_dna(company, *, dna_type=DNAGenerale.TYPE_COMPLETE):
    return DNAGenerale.objects.create(
        company=company,
        version=1,
        dna_type=dna_type,
        content={"identita": "Azienda tecnica"},
        audit_hash="company-dna-hash" if dna_type == DNAGenerale.TYPE_COMPLETE else "",
    )


def _product(company, *, slug="vasca"):
    return Specialista.objects.create(
        company=company,
        name=slug.title(),
        slug=slug,
        codice=slug.upper(),
    )


def _product_dna(product, *, proposals=None, dna_type=ProductDNA.TYPE_COMPLETE):
    content = {"identita_tecnica": f"DNA {product.name}"}
    if proposals is not None:
        content["_feedback_proposals"] = proposals
    return ProductDNA.objects.create(
        product=product,
        version=1,
        dna_type=dna_type,
        content=content,
        is_approved=timezone.now() if dna_type == ProductDNA.TYPE_COMPLETE else None,
    )


def _proposal(value):
    return {
        "target_layer": "nucleo_tecnico",
        "current_value": "Valore corrente",
        "proposed_value": value,
        "rationale": "Evidenza dal DNA Specialista",
    }


def _company_gap_question(company, pre_dna):
    return CompanyQuestion.objects.create(
        company=company,
        dna=pre_dna,
        code="F1",
        section_key="identita",
        principle="Confine operativo",
        question="Quale confine applichi?",
        question_round=2,
    )


def _product_gap_question(product, pre_dna):
    return ProductQuestion.objects.create(
        product=product,
        dna=pre_dna,
        code="F1",
        section_key="vincoli",
        principle="Confine tecnico",
        question="Quale vincolo applichi?",
        question_round=2,
    )


@pytest.mark.parametrize("target", ["company", "product"])
def test_gap_round_get_is_side_effect_free(paid_workspace, target):
    company = paid_workspace.company
    if target == "company":
        pre_dna = _company_dna(company, dna_type=DNAGenerale.TYPE_PRE)
        _company_gap_question(company, pre_dna)
        request = paid_workspace.request("get", reverse("dna-gap-questions", args=[2]))
        task_path = "apps.companies.tasks.process_company_gap_round_task.delay"
        call = partial(views.dna_gap_questions, request, 2)
    else:
        product = _product(company)
        pre_dna = _product_dna(product, dna_type=ProductDNA.TYPE_PRE)
        _product_gap_question(product, pre_dna)
        request = paid_workspace.request(
            "get",
            reverse("specialista-gap-questions", args=[product.pk, 2]),
        )
        task_path = "apps.companies.tasks.process_product_gap_round_task.delay"
        call = partial(views.product_gap_questions, request, product.pk, 2)

    with patch(task_path) as delay:
        response = call()

    assert response.status_code == 200
    delay.assert_not_called()
    assert not PaidOperation.objects.exists()


@pytest.mark.parametrize("target", ["company", "product"])
def test_gap_round_rejects_arbitrary_round_without_dispatch(paid_workspace, target):
    company = paid_workspace.company
    if target == "company":
        pre_dna = _company_dna(company, dna_type=DNAGenerale.TYPE_PRE)
        question = _company_gap_question(company, pre_dna)
        request = paid_workspace.request(
            "post",
            reverse("dna-gap-questions", args=[3]),
            {f"answer_{question.pk}": "Risposta"},
            form=True,
        )
        task_path = "apps.companies.tasks.process_company_gap_round_task.delay"
        call = partial(views.dna_gap_questions, request, 3)
    else:
        product = _product(company)
        pre_dna = _product_dna(product, dna_type=ProductDNA.TYPE_PRE)
        question = _product_gap_question(product, pre_dna)
        request = paid_workspace.request(
            "post",
            reverse("specialista-gap-questions", args=[product.pk, 3]),
            {f"answer_{question.pk}": "Risposta"},
            form=True,
        )
        task_path = "apps.companies.tasks.process_product_gap_round_task.delay"
        call = partial(views.product_gap_questions, request, product.pk, 3)

    with patch(task_path) as delay:
        response = call()

    assert response.status_code == 409
    delay.assert_not_called()
    assert not PaidOperation.objects.exists()


def test_duplicate_product_generation_queues_one_operation_and_one_task(paid_workspace):
    product = _product(paid_workspace.company)
    ProductFile.objects.create(
        product=product,
        original_name="scheda.txt",
        content_text="Scheda tecnica",
        file_size=14,
    )
    path = reverse("specialista-dna-generate", args=[product.pk])

    with patch("apps.companies.tasks.generate_product_dna_task.delay") as delay:
        first = views.product_dna_generate(
            paid_workspace.request("post", path, form=True),
            product.pk,
        )
        second = views.product_dna_generate(
            paid_workspace.request("post", path, form=True),
            product.pk,
        )

    assert first.status_code == 302
    assert second.status_code == 302
    delay.assert_called_once()
    operation = PaidOperation.objects.get(kind="product_generate")
    assert operation.status == PaidOperation.STATUS_QUEUED
    assert operation.payload["product_id"] == product.pk
    product.refresh_from_db()
    assert product.status == Specialista.STATUS_IN_COSTRUZIONE


@pytest.mark.parametrize(
    "endpoint",
    [
        "onboarding_pipeline",
        "source_scrape",
        "pipeline",
        "company_dna",
        "product_dna",
        "company_gap",
        "product_gap",
        "consistency",
        "product_consistency",
        "feedback_generate",
        "feedback_apply",
    ],
)
def test_suspended_workspace_blocks_every_paid_endpoint(paid_workspace, endpoint):
    company = paid_workspace.company
    request = paid_workspace.request

    if endpoint == "onboarding_pipeline":
        req = request(
            "post",
            reverse("onboarding-source-create"),
            {"url": "https://example.com"},
            form=True,
        )
        call = partial(views.onboarding_source_create, req)
    elif endpoint == "source_scrape":
        req = request(
            "post",
            reverse("source-list-create"),
            {"url": "https://example.com"},
        )
        call = partial(views.source_list_create, req)
    elif endpoint == "pipeline":
        source = Source.objects.create(company=company, url="https://example.com")
        req = request(
            "post",
            reverse("pipeline-run-create"),
            {"source_id": source.pk},
        )
        call = partial(views.pipeline_run_create, req)
    elif endpoint == "company_dna":
        source = Source.objects.create(
            company=company,
            url="https://example.com",
            status=Source.STATUS_SCRAPED,
            scraped_data={"markdown": "Contenuto"},
        )
        req = request(
            "post",
            reverse("dna-generate"),
            {"source_id": source.pk},
        )
        call = partial(views.dna_generate, req)
    elif endpoint == "product_dna":
        product = _product(company)
        ProductFile.objects.create(
            product=product,
            original_name="scheda.txt",
            content_text="Scheda tecnica",
        )
        req = request(
            "post",
            reverse("specialista-dna-generate", args=[product.pk]),
            form=True,
        )
        call = partial(views.product_dna_generate, req, product.pk)
    elif endpoint == "company_gap":
        pre_dna = _company_dna(company, dna_type=DNAGenerale.TYPE_PRE)
        _company_gap_question(company, pre_dna)
        req = request("get", reverse("dna-gap-questions", args=[2]))
        call = partial(views.dna_gap_questions, req, 2)
    elif endpoint == "product_gap":
        product = _product(company)
        pre_dna = _product_dna(product, dna_type=ProductDNA.TYPE_PRE)
        _product_gap_question(product, pre_dna)
        req = request(
            "get",
            reverse("specialista-gap-questions", args=[product.pk, 2]),
        )
        call = partial(views.product_gap_questions, req, product.pk, 2)
    elif endpoint == "consistency":
        _company_dna(company)
        req = request("post", reverse("consistency-audit-run"), form=True)
        call = partial(views.consistency_audit_run, req)
    elif endpoint == "product_consistency":
        _company_dna(company)
        product = _product(company)
        _product_dna(product)
        req = request(
            "post",
            reverse("specialista-consistency-check", args=[product.pk]),
            form=True,
        )
        call = partial(views.product_consistency_check, req, product.pk)
    elif endpoint == "feedback_generate":
        _company_dna(company)
        product = _product(company)
        _product_dna(product)
        req = request(
            "post",
            reverse("specialista-dna-feedback", args=[product.pk]),
            form=True,
        )
        call = partial(views.product_dna_feedback, req, product.pk)
    else:
        _company_dna(company)
        product = _product(company)
        _product_dna(product, proposals=[_proposal("Proposta sospesa")])
        req = request(
            "post",
            reverse("specialista-dna-feedback-apply", args=[product.pk]),
            {"selected_proposals": ["0"]},
            form=True,
        )
        call = partial(views.product_dna_feedback_apply, req, product.pk)

    paid_workspace.subscription.status = WorkspaceSubscription.STATUS_SUSPENDED
    paid_workspace.subscription.save(update_fields=["status"])

    response = call()

    assert response.status_code == 403
    assert "Workspace sospeso" in response.content.decode()
    assert not PaidOperation.objects.exists()
    assert not PipelineRun.objects.exists()


@pytest.mark.parametrize(
    "worker",
    [
        "source_scrape",
        "company_pipeline",
        "product_generate",
        "product_questions",
        "company_gap",
        "company_complete",
        "product_gap",
        "product_complete",
        "feedback_generate",
        "feedback_apply",
        "consistency_audit",
    ],
)
def test_every_paid_worker_rechecks_suspension_before_work(paid_workspace, worker):
    company = paid_workspace.company
    payloads = {
        "source_scrape": ("source_scrape", {"source_id": 999}),
        "company_pipeline": ("company_pipeline", {"pipeline_run_id": 999}),
        "product_generate": ("product_generate", {"product_id": 999, "source_files": []}),
        "product_questions": ("product_generate", {"product_id": 999}),
        "company_gap": (
            "company_gap",
            {"company_id": company.pk, "pre_dna_id": 999, "round": 1},
        ),
        "company_complete": (
            "company_gap",
            {"company_id": company.pk, "pre_dna_id": 999, "round": 1},
        ),
        "product_gap": (
            "product_gap",
            {"product_id": 999, "pre_dna_id": 999, "round": 1},
        ),
        "product_complete": (
            "product_gap",
            {"product_id": 999, "pre_dna_id": 999, "round": 1},
        ),
        "feedback_generate": (
            "specialist_feedback_generate",
            {"product_id": 999, "specialist_dna_id": 999, "company_dna_id": 999},
        ),
        "feedback_apply": (
            "specialist_feedback_apply",
            {"company_id": company.pk, "company_dna_id": 999},
        ),
        "consistency_audit": (
            "consistency_audit",
            {
                "company_id": company.pk,
                "scope": ConsistencyIssue.SCOPE_PERIODIC,
                "product_id": None,
            },
        ),
    }
    kind, payload = payloads[worker]
    operation = PaidOperation.objects.create(
        company=company,
        kind=kind,
        idempotency_key=f"{worker}-key",
        payload=payload,
    )
    paid_workspace.subscription.status = WorkspaceSubscription.STATUS_SUSPENDED
    paid_workspace.subscription.save(update_fields=["status"])

    calls = {
        "source_scrape": lambda: tasks.scrape_source(999, operation_id=operation.pk),
        "company_pipeline": lambda: tasks.run_pipeline(999, operation_id=operation.pk),
        "product_generate": lambda: tasks.generate_product_dna_task(
            999, operation_id=operation.pk
        ),
        "product_questions": lambda: tasks.generate_product_questions_task(
            999, 999, operation_id=operation.pk
        ),
        "company_gap": lambda: tasks.process_company_gap_round_task(
            company.pk, 999, 1, operation_id=operation.pk
        ),
        "company_complete": lambda: tasks.generate_complete_dna(
            company.pk, 999, None, operation_id=operation.pk
        ),
        "product_gap": lambda: tasks.process_product_gap_round_task(
            999, 999, 1, operation_id=operation.pk
        ),
        "product_complete": lambda: tasks.generate_complete_product_dna(
            999, 999, None, operation_id=operation.pk
        ),
        "feedback_generate": lambda: tasks.generate_specialist_feedback_task(
            999, 999, 999, operation_id=operation.pk
        ),
        "feedback_apply": lambda: tasks.apply_specialist_feedback_task(
            company.pk, 999, operation_id=operation.pk
        ),
        "consistency_audit": lambda: tasks.run_consistency_audit(
            company.pk,
            scope=ConsistencyIssue.SCOPE_PERIODIC,
            operation_id=operation.pk,
        ),
    }

    calls[worker]()

    operation.refresh_from_db()
    assert operation.status == PaidOperation.STATUS_REJECTED
    assert operation.error_code == "workspace_suspended"


def test_suspended_product_worker_restores_a_retryable_product_state(paid_workspace):
    product = _product(paid_workspace.company)
    product.status = Specialista.STATUS_IN_COSTRUZIONE
    product.generation_step = "1/5: Concept Map"
    product.save(update_fields=["status", "generation_step"])
    operation = PaidOperation.objects.create(
        company=paid_workspace.company,
        kind="product_generate",
        idempotency_key="suspended-product",
        resource_key=f"product-generate:{product.pk}",
        payload={
            "product_id": product.pk,
            "source_files": [],
            "source_status": Specialista.STATUS_BOZZA,
        },
    )
    paid_workspace.subscription.status = WorkspaceSubscription.STATUS_SUSPENDED
    paid_workspace.subscription.save(update_fields=["status"])

    tasks.generate_product_dna_task(product.pk, operation_id=operation.pk)

    product.refresh_from_db()
    operation.refresh_from_db()
    assert operation.status == PaidOperation.STATUS_REJECTED
    assert product.status == Specialista.STATUS_BOZZA
    assert "workspace suspended" in product.generation_step.lower()


@pytest.mark.parametrize("target", ["company", "product"])
def test_suspended_gap_worker_marks_failure_and_allows_explicit_retry(
    paid_workspace,
    target,
):
    company = paid_workspace.company
    if target == "company":
        pre_dna = _company_dna(company, dna_type=DNAGenerale.TYPE_PRE)
        question = _company_gap_question(company, pre_dna)
        path = reverse("dna-gap-questions", args=[2])
        task_path = "apps.companies.tasks.process_company_gap_round_task.delay"
        call = partial(views.dna_gap_questions, round_number=2)
    else:
        product = _product(company)
        pre_dna = _product_dna(product, dna_type=ProductDNA.TYPE_PRE)
        question = _product_gap_question(product, pre_dna)
        path = reverse("specialista-gap-questions", args=[product.pk, 2])
        task_path = "apps.companies.tasks.process_product_gap_round_task.delay"
        call = partial(
            views.product_gap_questions,
            pk=product.pk,
            round_number=2,
        )

    with patch(task_path):
        response = call(
            paid_workspace.request(
                "post",
                path,
                {f"answer_{question.pk}": "Risposta salvata"},
                form=True,
            )
        )
    assert response.status_code == 302
    operation = PaidOperation.objects.get(
        kind="company_gap" if target == "company" else "product_gap"
    )

    paid_workspace.subscription.status = WorkspaceSubscription.STATUS_SUSPENDED
    paid_workspace.subscription.save(update_fields=["status"])
    if target == "company":
        tasks.process_company_gap_round_task(
            company.pk,
            pre_dna.pk,
            2,
            operation_id=operation.pk,
        )
    else:
        tasks.process_product_gap_round_task(
            product.pk,
            pre_dna.pk,
            2,
            operation_id=operation.pk,
        )

    pre_dna.refresh_from_db()
    state_key = "_async_processing" if target == "company" else "_gap_processing"
    assert pre_dna.content[state_key]["status"] == "failed"

    paid_workspace.subscription.status = WorkspaceSubscription.STATUS_ACTIVE
    paid_workspace.subscription.save(update_fields=["status"])
    with patch(task_path) as retry_delay:
        retry_response = call(
            paid_workspace.request(
                "post",
                path,
                {f"answer_{question.pk}": "Risposta salvata"},
                form=True,
            )
        )

    assert retry_response.status_code == 302
    retry_delay.assert_called_once()
    pre_dna.refresh_from_db()
    assert pre_dna.content[state_key]["status"] in {"queued", "running"}


def test_suspended_source_worker_marks_the_source_failed(paid_workspace):
    path = reverse("source-list-create")
    with patch("apps.companies.tasks.scrape_source.delay"):
        response = views.source_list_create(
            paid_workspace.request(
                "post",
                path,
                {"url": "https://example.com"},
            )
        )
    assert response.status_code == 201
    source = Source.objects.get()
    operation = PaidOperation.objects.get(kind="source_scrape")
    paid_workspace.subscription.status = WorkspaceSubscription.STATUS_SUSPENDED
    paid_workspace.subscription.save(update_fields=["status"])

    tasks.scrape_source(source.pk, operation_id=operation.pk)

    source.refresh_from_db()
    assert source.status == Source.STATUS_FAILED
    assert "workspace suspended" in source.error_msg.lower()


def test_suspended_pipeline_worker_marks_the_run_failed(paid_workspace):
    source = Source.objects.create(
        company=paid_workspace.company,
        url="https://example.com",
    )
    path = reverse("pipeline-run-create")
    with patch("apps.companies.tasks.run_pipeline.delay"):
        response = views.pipeline_run_create(
            paid_workspace.request("post", path, {"source_id": source.pk})
        )
    assert response.status_code == 201
    run = PipelineRun.objects.get()
    operation = PaidOperation.objects.get(kind="company_pipeline")
    paid_workspace.subscription.status = WorkspaceSubscription.STATUS_SUSPENDED
    paid_workspace.subscription.save(update_fields=["status"])

    tasks.run_pipeline(run.pk, operation_id=operation.pk)

    run.refresh_from_db()
    assert run.status == PipelineRun.STATUS_FAILED
    assert "workspace suspended" in run.error_msg.lower()


def test_suspended_consistency_worker_clears_the_pending_marker(paid_workspace):
    company_dna = _company_dna(paid_workspace.company)
    product = _product(paid_workspace.company)
    product.status = Specialista.STATUS_UPDATING
    product.save(update_fields=["status"])
    _product_dna(product)
    path = reverse("specialista-consistency-check", args=[product.pk])
    with patch("apps.companies.tasks.run_consistency_audit.delay"):
        response = views.product_consistency_check(
            paid_workspace.request("post", path, form=True),
            product.pk,
        )
    assert response.status_code == 302
    company_dna.refresh_from_db()
    assert "_consistency_audit_pending" in company_dna.content
    operation = PaidOperation.objects.get(kind="consistency_audit")
    paid_workspace.subscription.status = WorkspaceSubscription.STATUS_SUSPENDED
    paid_workspace.subscription.save(update_fields=["status"])

    tasks.run_consistency_audit(
        paid_workspace.company.pk,
        scope=ConsistencyIssue.SCOPE_SPECIALIST,
        product_id=product.pk,
        operation_id=operation.pk,
    )

    company_dna.refresh_from_db()
    product.refresh_from_db()
    assert "_consistency_audit_pending" not in company_dna.content
    assert product.status == Specialista.STATUS_ATTIVO


def test_suspended_feedback_generation_worker_clears_the_pending_slot(paid_workspace):
    company_dna = _company_dna(paid_workspace.company)
    product = _product(paid_workspace.company)
    specialist_dna = _product_dna(product)
    path = reverse("specialista-dna-feedback", args=[product.pk])
    with patch("apps.companies.tasks.generate_specialist_feedback_task.delay"):
        response = views.product_dna_feedback(
            paid_workspace.request("post", path, form=True),
            product.pk,
        )
    assert response.status_code == 302
    operation = PaidOperation.objects.get(kind="specialist_feedback_generate")
    paid_workspace.subscription.status = WorkspaceSubscription.STATUS_SUSPENDED
    paid_workspace.subscription.save(update_fields=["status"])

    tasks.generate_specialist_feedback_task(
        product.pk,
        specialist_dna.pk,
        company_dna.pk,
        operation_id=operation.pk,
    )

    specialist_dna.refresh_from_db()
    assert "_feedback_proposals" not in specialist_dna.content
    assert specialist_dna.content["_feedback_generation"]["status"] == "failed"

    paid_workspace.subscription.status = WorkspaceSubscription.STATUS_ACTIVE
    paid_workspace.subscription.save(update_fields=["status"])
    with patch("apps.companies.tasks.generate_specialist_feedback_task.delay") as retry_delay:
        retry_response = views.product_dna_feedback(
            paid_workspace.request("post", path, form=True),
            product.pk,
        )
    assert retry_response.status_code == 302
    retry_delay.assert_called_once()


def test_failed_feedback_generation_does_not_report_completed(paid_workspace):
    company_dna = _company_dna(paid_workspace.company)
    product = _product(paid_workspace.company)
    specialist_dna = _product_dna(product)
    path = reverse("specialista-dna-feedback", args=[product.pk])
    with patch("apps.companies.tasks.generate_specialist_feedback_task.delay"):
        response = views.product_dna_feedback(
            paid_workspace.request("post", path, form=True),
            product.pk,
        )
    assert response.status_code == 302
    operation = PaidOperation.objects.get(kind="specialist_feedback_generate")

    with patch(
        "apps.companies.views._generate_specialist_feedback_proposals",
        side_effect=RuntimeError("llm down"),
    ):
        tasks.generate_specialist_feedback_task(
            product.pk,
            specialist_dna.pk,
            company_dna.pk,
            operation_id=operation.pk,
        )

    specialist_dna.refresh_from_db()
    operation.refresh_from_db()
    assert specialist_dna.content["_feedback_generation"]["status"] == "failed"
    assert not specialist_dna.content.get("_feedback_proposals")
    assert operation.status == PaidOperation.STATUS_FAILED
    assert operation.error_code == "RuntimeError"


def test_suspended_feedback_apply_worker_restores_immutable_proposals(paid_workspace):
    company_dna = _company_dna(paid_workspace.company)
    product = _product(paid_workspace.company)
    proposals = [_proposal("Proposta da recuperare")]
    specialist_dna = _product_dna(product, proposals=proposals)
    path = reverse("specialista-dna-feedback-apply", args=[product.pk])
    with patch("apps.companies.tasks.apply_specialist_feedback_task.delay"):
        response = views.product_dna_feedback_apply(
            paid_workspace.request(
                "post",
                path,
                {"selected_proposals": ["0"]},
                form=True,
            ),
            product.pk,
        )
    assert response.status_code == 302
    operation = PaidOperation.objects.get(kind="specialist_feedback_apply")
    specialist_dna.refresh_from_db()
    assert "_feedback_proposals" not in specialist_dna.content
    paid_workspace.subscription.status = WorkspaceSubscription.STATUS_SUSPENDED
    paid_workspace.subscription.save(update_fields=["status"])

    tasks.apply_specialist_feedback_task(
        paid_workspace.company.pk,
        company_dna.pk,
        operation_id=operation.pk,
    )

    specialist_dna.refresh_from_db()
    company_dna.refresh_from_db()
    assert specialist_dna.content["_feedback_proposals"] == proposals
    assert company_dna.content["_complete_generation"]["status"] == "failed"

    paid_workspace.subscription.status = WorkspaceSubscription.STATUS_ACTIVE
    paid_workspace.subscription.save(update_fields=["status"])
    with patch("apps.companies.tasks.apply_specialist_feedback_task.delay") as retry_delay:
        retry_response = views.product_dna_feedback_apply(
            paid_workspace.request(
                "post",
                path,
                {"selected_proposals": ["0"]},
                form=True,
            ),
            product.pk,
        )
    assert retry_response.status_code == 302
    retry_delay.assert_called_once()
    assert PaidOperation.objects.filter(kind="specialist_feedback_apply").count() == 1


def test_concurrent_feedback_keeps_first_immutable_payload(paid_workspace):
    company = paid_workspace.company
    company_dna = _company_dna(company)
    first_product = _product(company, slug="vasca")
    second_product = _product(company, slug="pompa")
    first_dna = _product_dna(first_product, proposals=[_proposal("Proposta vasca")])
    _product_dna(second_product, proposals=[_proposal("Proposta pompa")])

    with patch("apps.companies.tasks.apply_specialist_feedback_task.delay") as delay:
        first_response = views.product_dna_feedback_apply(
            paid_workspace.request(
                "post",
                reverse("specialista-dna-feedback-apply", args=[first_product.pk]),
                {"selected_proposals": ["0"]},
                form=True,
            ),
            first_product.pk,
        )
        second_response = views.product_dna_feedback_apply(
            paid_workspace.request(
                "post",
                reverse("specialista-dna-feedback-apply", args=[second_product.pk]),
                {"selected_proposals": ["0"]},
                form=True,
            ),
            second_product.pk,
        )

    assert first_response.status_code == 302
    assert second_response.status_code == 302
    delay.assert_called_once()
    operation = PaidOperation.objects.get(kind="specialist_feedback_apply")
    assert operation.payload["product_id"] == first_product.pk
    assert operation.payload["specialist_dna_id"] == first_dna.pk
    assert operation.payload["selected_proposals"] == [_proposal("Proposta vasca")]
    assert operation.payload["company_dna_id"] == company_dna.pk
    company_dna.refresh_from_db()
    assert "_pending_specialist_feedback" not in company_dna.content
