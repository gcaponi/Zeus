import os
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from django.contrib.admin.views.decorators import staff_member_required
from django.db import connection
from django.db.models import Sum
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.companies.models import Company, CompanyDNA, LLMCall, PipelineRun, Product
from apps.core.models import Client, Plan, WorkspaceAccess, WorkspaceSubscription


def _check_database():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return True
    except Exception:
        return False


def _check_celery():
    try:
        from config.celery import app as celery_app

        inspect = celery_app.control.inspect(timeout=3.0)
        response = inspect.ping()
        return response is not None and len(response) > 0
    except Exception:
        return False


def _check_storage():
    try:
        upload_dir = Path(os.environ.get("MEDIA_ROOT", "uploads"))
        return upload_dir.exists() and os.access(upload_dir, os.W_OK)
    except Exception:
        return False


def _system_health():
    return {
        "database": _check_database(),
        "celery": _check_celery(),
        "storage": _check_storage(),
    }


@dataclass
class ClientMetrics:
    company_name: str = "-"
    onboarding_step: str = "Account"
    onboarding_tone: str = "info"
    dna_status: str = "Non avviato"
    dna_tone: str = "muted"
    products_count: int = 0
    llm_cost_month: float = 0.0
    pipeline_status: str = "-"
    pipeline_tone: str = "muted"
    has_warning: bool = False


def _tenant_context(schema_name):
    if hasattr(connection, "set_schema"):
        return schema_context(schema_name)
    return nullcontext()


def _month_start():
    now = timezone.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _primary_domain(client):
    for domain in client.domains.all():
        if domain.is_primary:
            return domain.domain
    first_domain = next(iter(client.domains.all()), None)
    return first_domain.domain if first_domain else ""


def _owner_email(domain):
    if not domain:
        return "-"
    return (
        WorkspaceAccess.objects.filter(tenant_domain=domain)
        .values_list(
            "email",
            flat=True,
        )
        .first()
        or "-"
    )


def _subscription_tone(subscription):
    if not subscription:
        return "warning"
    if subscription.status == WorkspaceSubscription.STATUS_ACTIVE:
        return "online"
    if subscription.status == WorkspaceSubscription.STATUS_TRIAL:
        return "info"
    return "error"


def _pipeline_tone(status):
    return {
        PipelineRun.STATUS_COMPLETED: "online",
        PipelineRun.STATUS_RUNNING: "info",
        PipelineRun.STATUS_PENDING: "warning",
        PipelineRun.STATUS_FAILED: "error",
    }.get(status, "muted")


def _company_metrics_for_client(client):
    metrics = ClientMetrics()
    try:
        with _tenant_context(client.schema_name):
            company = Company.objects.filter(schema_name=client.schema_name).first()
            if not company:
                metrics.onboarding_step = "Sito web"
                return metrics

            metrics.company_name = company.name
            latest_pipeline = company.pipeline_runs.select_related("source").first()
            latest_dna = company.dna_versions.filter(is_current=True).first()
            complete_dna = company.dna_versions.filter(
                dna_type=CompanyDNA.TYPE_COMPLETE,
                is_current=True,
            ).first()
            products = Product.objects.filter(company=company)

            metrics.products_count = products.count()
            metrics.llm_cost_month = (
                LLMCall.objects.filter(
                    company=company,
                    created_at__gte=_month_start(),
                ).aggregate(total=Sum("cost_usd"))["total"]
                or 0.0
            )

            if latest_pipeline:
                metrics.pipeline_status = latest_pipeline.status
                metrics.pipeline_tone = _pipeline_tone(latest_pipeline.status)
                if latest_pipeline.status == PipelineRun.STATUS_FAILED:
                    metrics.has_warning = True

            if complete_dna:
                metrics.onboarding_step = "DNA completo"
                metrics.onboarding_tone = "online"
                metrics.dna_status = "Approvato" if complete_dna.is_fully_approved() else "Review"
                metrics.dna_tone = "online" if complete_dna.is_fully_approved() else "warning"
            elif latest_dna:
                metrics.onboarding_step = "Domande"
                metrics.onboarding_tone = "warning"
                metrics.dna_status = "Pre-DNA"
                metrics.dna_tone = "info"
            elif latest_pipeline and latest_pipeline.status in {
                PipelineRun.STATUS_PENDING,
                PipelineRun.STATUS_RUNNING,
            }:
                metrics.onboarding_step = "Scrape"
                metrics.onboarding_tone = "info"
            elif latest_pipeline and latest_pipeline.status == PipelineRun.STATUS_FAILED:
                metrics.onboarding_step = "Errore scrape"
                metrics.onboarding_tone = "error"
            else:
                metrics.onboarding_step = "Sito web"

    except Exception:
        metrics.onboarding_step = "Tenant non leggibile"
        metrics.onboarding_tone = "error"
        metrics.pipeline_status = "error"
        metrics.pipeline_tone = "error"
        metrics.has_warning = True
    return metrics


def _client_rows(clients):
    rows = []
    for client in clients:
        domain = _primary_domain(client)
        subscription = getattr(client, "subscription", None)
        metrics = _company_metrics_for_client(client)
        rows.append(
            {
                "id": client.pk,
                "name": client.name,
                "schema_name": client.schema_name,
                "domain": domain,
                "workspace_url": f"https://{domain}/onboarding/" if domain else "",
                "owner_email": _owner_email(domain),
                "created_on": client.created_on,
                "plan": subscription.plan.name if subscription else "Nessun piano",
                "subscription_status": subscription.status if subscription else "missing",
                "subscription_tone": _subscription_tone(subscription),
                "onboarding_step": metrics.onboarding_step,
                "onboarding_tone": metrics.onboarding_tone,
                "dna_status": metrics.dna_status,
                "dna_tone": metrics.dna_tone,
                "products_count": metrics.products_count,
                "llm_cost_month": metrics.llm_cost_month,
                "pipeline_status": metrics.pipeline_status,
                "pipeline_tone": metrics.pipeline_tone,
                "has_warning": metrics.has_warning or not subscription,
            }
        )
    return rows


def _clients_queryset():
    return (
        Client.objects.prefetch_related("domains")
        .select_related(
            "subscription__plan",
        )
        .order_by("-created_on", "name")
    )


def _admin_url(name, **params):
    url = reverse(name)
    if not params:
        return url
    return f"{url}?{urlencode(params)}"


def _client_segments(rows, active_segment):
    segment_counts = {
        "all": len(rows),
        "active": sum(
            1 for row in rows if row["subscription_status"] == WorkspaceSubscription.STATUS_ACTIVE
        ),
        "onboarding": sum(1 for row in rows if row["onboarding_step"] != "DNA completo"),
        "complete_dna": sum(1 for row in rows if row["dna_status"] in {"Review", "Approvato"}),
        "warning": sum(1 for row in rows if row["has_warning"]),
        "suspended": sum(
            1
            for row in rows
            if row["subscription_status"] == WorkspaceSubscription.STATUS_SUSPENDED
        ),
    }
    labels = {
        "all": "Tutti",
        "active": "Attivi",
        "onboarding": "Onboarding",
        "complete_dna": "DNA completi",
        "warning": "Da controllare",
        "suspended": "Sospesi",
    }
    return [
        {
            "key": key,
            "label": labels[key],
            "count": segment_counts[key],
            "active": key == active_segment,
            "url": _admin_url("zeus-admin-clients", segment=key),
        }
        for key in labels
    ]


def _filter_client_rows(rows, params):
    query = params.get("q", "").strip().lower()
    segment = params.get("segment", "all")
    pipeline = params.get("pipeline", "")
    products = params.get("products", "")
    sort = params.get("sort", "")

    filtered = list(rows)
    if segment == "active":
        filtered = [
            row
            for row in filtered
            if row["subscription_status"] == WorkspaceSubscription.STATUS_ACTIVE
        ]
    elif segment == "onboarding":
        filtered = [row for row in filtered if row["onboarding_step"] != "DNA completo"]
    elif segment == "complete_dna":
        filtered = [row for row in filtered if row["dna_status"] in {"Review", "Approvato"}]
    elif segment == "warning":
        filtered = [row for row in filtered if row["has_warning"]]
    elif segment == "suspended":
        filtered = [
            row
            for row in filtered
            if row["subscription_status"] == WorkspaceSubscription.STATUS_SUSPENDED
        ]

    if pipeline:
        filtered = [row for row in filtered if row["pipeline_status"] == pipeline]
    if products == "any":
        filtered = [row for row in filtered if row["products_count"] > 0]
    if query:
        filtered = [row for row in filtered if _client_row_matches(row, query)]
    if sort == "llm_desc":
        filtered = sorted(filtered, key=lambda row: row["llm_cost_month"], reverse=True)
    return filtered


def _client_row_matches(row, query):
    values = [
        row["name"],
        row["schema_name"],
        row["domain"],
        row["owner_email"],
        row["plan"],
        row["subscription_status"],
        row["onboarding_step"],
        row["dna_status"],
    ]
    return any(query in str(value).lower() for value in values)


def _client_rows_context(request):
    rows = _client_rows(list(_clients_queryset()))
    active_segment = request.GET.get("segment", "all")
    filtered_rows = _filter_client_rows(rows, request.GET)
    return {
        "clients": filtered_rows,
        "total_clients": len(rows),
        "visible_clients": len(filtered_rows),
        "segments": _client_segments(rows, active_segment),
        "active_segment": active_segment,
        "q": request.GET.get("q", ""),
        "pipeline": request.GET.get("pipeline", ""),
        "products": request.GET.get("products", ""),
        "sort": request.GET.get("sort", ""),
    }


@staff_member_required
def dashboard(request):
    clients = list(_clients_queryset())
    rows = _client_rows(clients)

    active_clients = sum(
        1 for row in rows if row["subscription_status"] == WorkspaceSubscription.STATUS_ACTIVE
    )
    onboarding_clients = sum(1 for row in rows if row["onboarding_step"] not in {"DNA completo"})
    complete_dnas = sum(1 for row in rows if row["dna_status"] in {"Review", "Approvato"})
    products_count = sum(row["products_count"] for row in rows)
    llm_cost_month = sum(row["llm_cost_month"] for row in rows)
    warnings_count = sum(1 for row in rows if row["has_warning"])
    pipeline_failures = sum(
        1 for row in rows if row["pipeline_status"] == PipelineRun.STATUS_FAILED
    )

    active_pipelines = sum(
        1 for row in rows if row["pipeline_status"] == PipelineRun.STATUS_RUNNING
    )

    llm_cost_str = f"${llm_cost_month:.2f}"
    kpis = [
        {
            "label": "Clienti attivi",
            "value": active_clients,
            "tone": "lime",
            "icon": "👥",
            "url": _admin_url("zeus-admin-clients", segment="active"),
        },
        {
            "label": "DNA completi",
            "value": complete_dnas,
            "tone": "cyan",
            "icon": "🧬",
            "url": _admin_url("zeus-admin-clients", segment="complete_dna"),
        },
        {
            "label": "Costo LLM mese",
            "value": llm_cost_str,
            "tone": "lime",
            "icon": "💰",
            "url": _admin_url("zeus-admin-clients", sort="llm_desc"),
        },
        {
            "label": "Pipeline attive",
            "value": active_pipelines,
            "tone": "cyan",
            "icon": "🔄",
            "url": _admin_url("zeus-admin-clients", pipeline=PipelineRun.STATUS_RUNNING),
        },
        {
            "label": "Onboarding aperti",
            "value": onboarding_clients,
            "tone": "amber",
            "icon": "📋",
            "url": _admin_url("zeus-admin-clients", segment="onboarding"),
        },
        {
            "label": "Prodotti totali",
            "value": products_count,
            "tone": "violet",
            "icon": "📦",
            "url": _admin_url("zeus-admin-clients", products="any"),
        },
        {
            "label": "Pipeline fallite",
            "value": pipeline_failures,
            "tone": "red",
            "icon": "❌",
            "url": _admin_url("zeus-admin-clients", pipeline=PipelineRun.STATUS_FAILED),
        },
        {
            "label": "Da controllare",
            "value": warnings_count,
            "tone": "amber",
            "icon": "⚠️",
            "url": _admin_url("zeus-admin-clients", segment="warning"),
        },
    ]
    context = {
        "kpis": kpis,
        "clients": rows,
        "attention_clients": [row for row in rows if row["has_warning"]][:6],
        "plans": [
            {
                "name": plan.name,
                "slug": plan.slug,
                "subscriptions": plan.subscriptions.count(),
            }
            for plan in Plan.objects.prefetch_related("subscriptions")
        ],
        "system_health": _system_health(),
    }
    return render(request, "zeus_admin/dashboard.html", context)


@staff_member_required
def clients(request):
    context = _client_rows_context(request)
    template = "zeus_admin/clients.html"
    if request.headers.get("HX-Request"):
        template = "zeus_admin/_clients_results.html"
    return render(request, template, context)
