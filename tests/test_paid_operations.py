from datetime import timedelta

import pytest
from django.utils import timezone

from apps.companies.models import Company, PaidOperation
from apps.companies.operations import (
    OperationRejectedError,
    claim_operation,
    complete_operation,
    fail_operation,
    operation_key,
    reserve_operation,
)
from apps.core.models import Client, Plan, WorkspaceSubscription

pytestmark = pytest.mark.django_db


@pytest.fixture
def paid_company(monkeypatch):
    monkeypatch.setattr(Client, "auto_create_schema", False)
    tenant = Client.objects.create(schema_name="paid", name="Paid")
    plan, _ = Plan.objects.update_or_create(
        slug=Plan.SLUG_STARTER,
        defaults=Plan.default_values(Plan.SLUG_STARTER),
    )
    WorkspaceSubscription.objects.create(
        client=tenant,
        plan=plan,
        status=WorkspaceSubscription.STATUS_ACTIVE,
    )
    return Company.objects.create(schema_name="paid", name="Paid")


def test_duplicate_idempotency_key_returns_existing_operation(paid_company):
    key = operation_key("product", 1, "source-v1")

    first, first_created = reserve_operation(
        paid_company,
        "product_generate",
        key,
        resource_key="product:1",
    )
    second, second_created = reserve_operation(
        paid_company,
        "product_generate",
        key,
        resource_key="product:1",
    )

    assert first_created is True
    assert second_created is False
    assert second.pk == first.pk
    assert PaidOperation.objects.count() == 1


def test_resource_allows_only_one_active_operation(paid_company):
    first, _ = reserve_operation(
        paid_company,
        "consistency_audit",
        operation_key("audit", 1),
        resource_key="company-dna:1",
    )

    second, created = reserve_operation(
        paid_company,
        "specialist_feedback_apply",
        operation_key("feedback", 2),
        resource_key="company-dna:1",
    )

    assert created is False
    assert second.pk == first.pk


def test_concurrency_limit_rejects_before_third_operation(paid_company, settings):
    settings.PAID_OPERATION_CONCURRENCY_LIMITS = {Plan.SLUG_STARTER: 2}
    reserve_operation(paid_company, "agent_chat", operation_key("a"))
    reserve_operation(paid_company, "guide_chat", operation_key("b"))

    with pytest.raises(OperationRejectedError) as exc_info:
        reserve_operation(paid_company, "source_scrape", operation_key("c"))

    assert exc_info.value.rejection.code == "operation_concurrency_limit"


def test_stale_cleanup_survives_concurrency_rejection(paid_company, settings):
    settings.PAID_OPERATION_STALE_SECONDS = 60
    settings.PAID_OPERATION_CONCURRENCY_LIMITS = {Plan.SLUG_STARTER: 2}
    stale, _ = reserve_operation(
        paid_company,
        "agent_chat",
        operation_key("stale-slot"),
    )
    reserve_operation(paid_company, "guide_chat", operation_key("live-slot"))
    PaidOperation.objects.filter(pk=stale.pk).update(
        created_at=timezone.now() - timedelta(minutes=5)
    )
    settings.PAID_OPERATION_CONCURRENCY_LIMITS = {Plan.SLUG_STARTER: 1}

    with pytest.raises(OperationRejectedError) as exc_info:
        reserve_operation(paid_company, "source_scrape", operation_key("over-limit"))

    stale.refresh_from_db()
    assert exc_info.value.rejection.code == "operation_concurrency_limit"
    assert stale.status == PaidOperation.STATUS_FAILED
    assert stale.error_code == "stale_operation"


def test_daily_units_are_reserved_atomically(paid_company, settings):
    settings.PAID_OPERATION_DAILY_UNIT_LIMITS = {Plan.SLUG_STARTER: 2}
    first, _ = reserve_operation(
        paid_company,
        "source_scrape",
        operation_key("source", 1),
    )
    claimed = claim_operation(first.pk, "source_scrape")
    complete_operation(claimed.pk)

    with pytest.raises(OperationRejectedError) as exc_info:
        reserve_operation(paid_company, "agent_chat", operation_key("agent", 1))

    assert exc_info.value.rejection.code == "operation_daily_limit"


def test_worker_claim_rechecks_suspended_subscription(paid_company):
    operation, _ = reserve_operation(
        paid_company,
        "product_generate",
        operation_key("product", 1),
    )
    subscription = WorkspaceSubscription.objects.get(client__schema_name="paid")
    subscription.status = WorkspaceSubscription.STATUS_SUSPENDED
    subscription.save(update_fields=["status"])

    claimed = claim_operation(operation.pk, "product_generate")

    assert claimed is None
    operation.refresh_from_db()
    assert operation.status == PaidOperation.STATUS_REJECTED
    assert operation.error_code == "workspace_suspended"


def test_failed_operation_can_retry_with_the_same_immutable_payload(paid_company):
    payload = {"product_id": 7, "source_files": [[3, 128]]}
    operation, _ = reserve_operation(
        paid_company,
        "product_generate",
        operation_key("product", 7, payload["source_files"]),
        payload=payload,
        resource_key="product-generate:7",
    )
    claim_operation(operation.pk, "product_generate")
    fail_operation(operation.pk, "temporary_failure")

    retried, created = reserve_operation(
        paid_company,
        "product_generate",
        operation.idempotency_key,
        payload=payload,
        resource_key="product-generate:7",
    )

    retried.refresh_from_db()
    assert created is True
    assert retried.pk == operation.pk
    assert retried.status == PaidOperation.STATUS_QUEUED
    assert retried.payload == payload
    assert retried.error_code == ""
    assert retried.started_at is None
    assert retried.finished_at is None


def test_retry_counts_the_reservation_once_against_daily_budget(
    paid_company,
    settings,
):
    settings.PAID_OPERATION_DAILY_UNIT_LIMITS = {Plan.SLUG_STARTER: 2}
    payload = {"source_id": 1}
    operation, _ = reserve_operation(
        paid_company,
        "source_scrape",
        operation_key("source", 1),
        payload=payload,
    )
    claim_operation(operation.pk, "source_scrape")
    fail_operation(operation.pk, "temporary_failure")

    retried, created = reserve_operation(
        paid_company,
        "source_scrape",
        operation.idempotency_key,
        payload=payload,
    )

    assert created is True
    assert retried.pk == operation.pk


def test_retry_never_overwrites_the_immutable_payload(paid_company):
    operation, _ = reserve_operation(
        paid_company,
        "source_scrape",
        operation_key("source", 1),
        payload={"source_id": 1, "url": "https://original.example"},
        resource_key="source-scrape:1",
    )
    claim_operation(operation.pk, "source_scrape")
    fail_operation(operation.pk, "temporary_failure")

    existing, created = reserve_operation(
        paid_company,
        "source_scrape",
        operation.idempotency_key,
        payload={"source_id": 1, "url": "https://changed.example"},
        resource_key="source-scrape:1",
    )

    existing.refresh_from_db()
    assert created is False
    assert existing.status == PaidOperation.STATUS_FAILED
    assert existing.payload["url"] == "https://original.example"


def test_stale_duplicate_is_requeued_instead_of_blocking_forever(
    paid_company,
    settings,
):
    settings.PAID_OPERATION_STALE_SECONDS = 60
    payload = {"source_id": 1, "url": "https://example.com"}
    operation, _ = reserve_operation(
        paid_company,
        "source_scrape",
        operation_key("source", 1),
        payload=payload,
        resource_key="source-scrape:1",
    )
    stale_created_at = timezone.now() - timedelta(minutes=5)
    PaidOperation.objects.filter(pk=operation.pk).update(created_at=stale_created_at)

    retried, created = reserve_operation(
        paid_company,
        "source_scrape",
        operation.idempotency_key,
        payload=payload,
        resource_key="source-scrape:1",
    )

    retried.refresh_from_db()
    assert created is True
    assert retried.pk == operation.pk
    assert retried.status == PaidOperation.STATUS_QUEUED
    assert retried.created_at > stale_created_at


def test_stale_resource_lock_does_not_block_a_new_operation(paid_company, settings):
    settings.PAID_OPERATION_STALE_SECONDS = 60
    stale, _ = reserve_operation(
        paid_company,
        "consistency_audit",
        operation_key("audit", "old"),
        resource_key="company-dna:1",
    )
    PaidOperation.objects.filter(pk=stale.pk).update(
        created_at=timezone.now() - timedelta(minutes=5)
    )

    fresh, created = reserve_operation(
        paid_company,
        "specialist_feedback_apply",
        operation_key("feedback", "new"),
        resource_key="company-dna:1",
    )

    stale.refresh_from_db()
    assert created is True
    assert fresh.pk != stale.pk
    assert stale.status == PaidOperation.STATUS_FAILED
    assert stale.error_code == "stale_operation"


def test_running_operation_is_not_expired_from_created_at(paid_company, settings):
    settings.PAID_OPERATION_STALE_SECONDS = 60
    operation, _ = reserve_operation(
        paid_company,
        "source_scrape",
        operation_key("source", "running"),
        payload={"source_id": 1, "url": "https://example.com"},
        resource_key="source-scrape:1",
    )
    claim_operation(operation.pk, "source_scrape")
    now = timezone.now()
    PaidOperation.objects.filter(pk=operation.pk).update(
        created_at=now - timedelta(minutes=5),
        started_at=now - timedelta(seconds=10),
    )

    same, created = reserve_operation(
        paid_company,
        "source_scrape",
        operation_key("source", "other"),
        payload={"source_id": 2, "url": "https://other.example"},
        resource_key="source-scrape:1",
    )

    operation.refresh_from_db()
    assert created is False
    assert same.pk == operation.pk
    assert operation.status == PaidOperation.STATUS_RUNNING
    assert operation.error_code == ""
    complete_operation(operation.pk, result={"ok": True}, actual_cost_usd="0.12")
    operation.refresh_from_db()
    assert operation.status == PaidOperation.STATUS_COMPLETED
    assert operation.result == {"ok": True}
    assert str(operation.actual_cost_usd) == "0.120000"


def test_running_operation_expires_from_started_at(paid_company, settings):
    settings.PAID_OPERATION_STALE_SECONDS = 60
    stale, _ = reserve_operation(
        paid_company,
        "consistency_audit",
        operation_key("audit", "running-old"),
        resource_key="company-dna:1",
    )
    claim_operation(stale.pk, "consistency_audit")
    now = timezone.now()
    PaidOperation.objects.filter(pk=stale.pk).update(
        created_at=now - timedelta(minutes=10),
        started_at=now - timedelta(minutes=5),
    )

    fresh, created = reserve_operation(
        paid_company,
        "specialist_feedback_apply",
        operation_key("feedback", "after-hang"),
        resource_key="company-dna:1",
    )

    stale.refresh_from_db()
    assert created is True
    assert fresh.pk != stale.pk
    assert stale.status == PaidOperation.STATUS_FAILED
    assert stale.error_code == "stale_operation"
    complete_operation(stale.pk, result={"lost": True}, actual_cost_usd="1.00")
    stale.refresh_from_db()
    assert stale.status == PaidOperation.STATUS_FAILED
    assert stale.result == {}
