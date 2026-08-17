import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.companies.models import Company, PaidOperation
from apps.core.models import Plan, WorkspaceSubscription


OPERATION_UNITS = {
    "agent_chat": 1,
    "guide_chat": 1,
    "source_scrape": 2,
    "company_pipeline": 8,
    "company_dna_generate": 5,
    "company_gap": 3,
    "product_generate": 12,
    "product_gap": 3,
    "consistency_audit": 3,
    "specialist_feedback_generate": 2,
    "specialist_feedback_apply": 5,
}


@dataclass(frozen=True)
class OperationRejection:
    code: str
    detail: str
    status_code: int


class OperationRejectedError(Exception):
    def __init__(self, rejection):
        self.rejection = rejection
        super().__init__(rejection.detail)


def operation_key(*parts):
    encoded = json.dumps(parts, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _subscription(company):
    return WorkspaceSubscription.objects.select_related("plan").filter(
        client__schema_name=company.schema_name,
    ).first()


def workspace_rejection(company):
    subscription = _subscription(company)
    if subscription and not subscription.can_use_workspace():
        return OperationRejection(
            "workspace_suspended",
            "Workspace sospeso. Contatta l'amministratore ZEUS.",
            403,
        )
    return None


def _plan_slug(company):
    subscription = _subscription(company)
    if subscription and subscription.plan:
        return subscription.plan.slug
    return Plan.SLUG_STARTER


def _limits(company):
    plan_slug = _plan_slug(company)
    daily = settings.PAID_OPERATION_DAILY_UNIT_LIMITS.get(
        plan_slug,
        settings.PAID_OPERATION_DAILY_UNIT_LIMITS[Plan.SLUG_STARTER],
    )
    concurrent = settings.PAID_OPERATION_CONCURRENCY_LIMITS.get(
        plan_slug,
        settings.PAID_OPERATION_CONCURRENCY_LIMITS[Plan.SLUG_STARTER],
    )
    return daily, concurrent


def reserve_operation(
    company,
    kind,
    idempotency_key,
    *,
    payload=None,
    resource_key="",
    requested_by=None,
    units=None,
):
    rejection = workspace_rejection(company)
    if rejection:
        raise OperationRejectedError(rejection)

    units = units or OPERATION_UNITS[kind]
    now = timezone.now()
    stale_before = now - timedelta(seconds=settings.PAID_OPERATION_STALE_SECONDS)
    immutable_payload = payload or {}

    with transaction.atomic():
        locked_company = Company.objects.select_for_update().get(pk=company.pk)

        # Expire abandoned reservations before checking either the exact
        # idempotency key or the protected resource. Otherwise a stale row can
        # keep returning as an active duplicate forever.
        # QUEUED ages from created_at. RUNNING must age from started_at:
        # a job that waited in queue can have an old created_at while still
        # being executed, and complete_operation/requeue_operation only
        # transition STATUS_RUNNING.
        PaidOperation.objects.filter(company=locked_company).filter(
            Q(status=PaidOperation.STATUS_QUEUED, created_at__lt=stale_before)
            | Q(
                status=PaidOperation.STATUS_RUNNING,
                started_at__lt=stale_before,
            )
        ).update(
            status=PaidOperation.STATUS_FAILED,
            error_code="stale_operation",
            finished_at=now,
        )

        existing = PaidOperation.objects.filter(
            company=locked_company,
            kind=kind,
            idempotency_key=idempotency_key,
        ).first()
        retry_operation = None
        if existing and existing.status in (
            PaidOperation.STATUS_FAILED,
            PaidOperation.STATUS_REJECTED,
        ):
            # The row may be reused only for the exact immutable request that
            # originally reserved it. A colliding key must never replace its
            # payload or accounting metadata.
            if (
                existing.payload == immutable_payload
                and existing.resource_key == resource_key
                and existing.reserved_units == units
            ):
                retry_operation = existing
            else:
                return existing, False
        elif existing:
            return existing, False

        if resource_key:
            active_resource = PaidOperation.objects.filter(
                company=locked_company,
                resource_key=resource_key,
                status__in=PaidOperation.ACTIVE_STATUSES,
            ).exclude(pk=getattr(retry_operation, "pk", None)).first()
            if active_resource:
                return active_resource, False

        daily_limit, concurrency_limit = _limits(locked_company)
        active = PaidOperation.objects.filter(
            company=locked_company,
            status__in=PaidOperation.ACTIVE_STATUSES,
        ).count()
        if active >= concurrency_limit:
            raise OperationRejectedError(
                OperationRejection(
                    "operation_concurrency_limit",
                    "Troppe operazioni in corso. Attendi il completamento.",
                    429,
                )
            )

        since = now - timedelta(hours=24)
        billed_operations = PaidOperation.objects.filter(
            company=locked_company,
            created_at__gte=since,
        )
        if retry_operation is not None:
            billed_operations = billed_operations.exclude(pk=retry_operation.pk)
        used = billed_operations.aggregate(total=Sum("reserved_units"))["total"] or 0
        if used + units > daily_limit:
            raise OperationRejectedError(
                OperationRejection(
                    "operation_daily_limit",
                    "Limite giornaliero di operazioni raggiunto.",
                    429,
                )
            )

        if retry_operation is not None:
            PaidOperation.objects.filter(pk=retry_operation.pk).update(
                status=PaidOperation.STATUS_QUEUED,
                result={},
                actual_cost_usd=0,
                error_code="",
                created_at=now,
                started_at=None,
                finished_at=None,
            )
            retry_operation.refresh_from_db()
            operation = retry_operation
        else:
            operation = PaidOperation.objects.create(
                company=locked_company,
                kind=kind,
                idempotency_key=idempotency_key,
                resource_key=resource_key,
                payload=immutable_payload,
                reserved_units=units,
                requested_by=requested_by,
            )
    return operation, True


def claim_operation(operation_id, expected_kind):
    with transaction.atomic():
        operation = PaidOperation.objects.select_for_update().select_related("company").filter(
            pk=operation_id,
            kind=expected_kind,
        ).first()
        if operation is None or operation.status != PaidOperation.STATUS_QUEUED:
            return None
        rejection = workspace_rejection(operation.company)
        if rejection:
            operation.status = PaidOperation.STATUS_REJECTED
            operation.error_code = rejection.code
            operation.finished_at = timezone.now()
            operation.save(update_fields=["status", "error_code", "finished_at"])
            return None
        operation.status = PaidOperation.STATUS_RUNNING
        operation.started_at = timezone.now()
        operation.save(update_fields=["status", "started_at"])
        return operation


def complete_operation(operation_id, *, result=None, actual_cost_usd=0):
    PaidOperation.objects.filter(
        pk=operation_id,
        status=PaidOperation.STATUS_RUNNING,
    ).update(
        status=PaidOperation.STATUS_COMPLETED,
        result=result or {},
        actual_cost_usd=Decimal(str(actual_cost_usd or 0)),
        error_code="",
        finished_at=timezone.now(),
    )


def requeue_operation(operation_id, stage):
    """Transferisce atomicamente un'operazione al task figlio successivo."""
    return PaidOperation.objects.filter(
        pk=operation_id,
        status=PaidOperation.STATUS_RUNNING,
    ).update(
        status=PaidOperation.STATUS_QUEUED,
        result={"stage": stage},
        started_at=None,
    ) == 1


def fail_operation(operation_id, error_code):
    PaidOperation.objects.filter(
        pk=operation_id,
        status__in=PaidOperation.ACTIVE_STATUSES,
    ).update(
        status=PaidOperation.STATUS_FAILED,
        error_code=str(error_code)[:100],
        finished_at=timezone.now(),
    )
