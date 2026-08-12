import pytest

from apps.companies.models import Company, PaidOperation
from apps.companies.operations import (
    OperationRejectedError,
    claim_operation,
    complete_operation,
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
