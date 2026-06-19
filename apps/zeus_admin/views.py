import json
import os
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.db import connection
from django.db.models import Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django_tenants.utils import schema_context

from apps.companies.models import (
    Company,
    CompanyDNA,
    CompanyFile,
    LLMCall,
    PipelineRun,
    Product,
    ProductDNA,
    ProductFile,
)
from apps.core.models import Client, Plan, WorkspaceAccess, WorkspaceSubscription

DEFAULT_EXCLUDED_DOMAINS = {"zeus.cais.uno"}


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


def _excluded_domains():
    return set(getattr(settings, "ZEUS_ADMIN_EXCLUDED_DOMAINS", DEFAULT_EXCLUDED_DOMAINS))


DNA_SECTION_LABELS = {
    "chi_siamo": "Chi Siamo",
    "mission": "Mission",
    "settore": "Settore",
    "mercato": "Mercato",
    "pilastri": "Pilastri",
}


def _dna_content_text(dna):
    if isinstance(dna.content, str):
        return dna.content
    return json.dumps(dna.content or {}, ensure_ascii=False, indent=2)


def _dna_content_dict(dna):
    if isinstance(dna.content, dict):
        return dna.content
    if isinstance(dna.content, str):
        try:
            parsed = json.loads(dna.content)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _admin_datetime(value):
    if not value:
        return "-"
    return timezone.localtime(value).strftime("%d/%m/%Y %H:%M")


def _content_response(title, meta, content):
    return JsonResponse(
        {
            "title": title,
            "meta": meta,
            "content": content or "Nessun contenuto testuale disponibile.",
        },
    )


def _dna_response(title, meta, dna):
    sections = []
    content = _dna_content_dict(dna)
    ordered_keys = ["chi_siamo", "mission", "settore", "mercato", "pilastri"]
    for key in ordered_keys:
        value = content.get(key)
        if not value:
            continue
        label = DNA_SECTION_LABELS.get(key, key)
        if isinstance(value, list):
            items = [str(item) for item in value if item]
            sections.append({"label": label, "items": items})
        else:
            sections.append({"label": label, "text": str(value)})
    for key, value in content.items():
        if key in ordered_keys or not value:
            continue
        label = DNA_SECTION_LABELS.get(key, key.replace("_", " ").title())
        if isinstance(value, list):
            items = [str(item) for item in value if item]
            sections.append({"label": label, "items": items})
        else:
            sections.append({"label": label, "text": str(value)})
    return JsonResponse(
        {
            "title": title,
            "meta": meta,
            "content": "",
            "sections": sections,
        },
    )


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
                "detail_url": reverse("zeus-admin-client-detail", args=[client.pk]),
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
    queryset = Client.objects.prefetch_related("domains").select_related(
        "subscription__plan",
    )
    excluded = _excluded_domains()
    if excluded:
        queryset = queryset.exclude(domains__domain__in=excluded).distinct()
    return queryset.order_by("-created_on", "name")


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


def _limit_label(plan, field, unlimited_field):
    if not plan:
        return "-"
    if getattr(plan, unlimited_field):
        return "Illimitati"
    return getattr(plan, field)


def _client_admin_links(client, domain):
    access_url = reverse("admin:core_workspaceaccess_changelist")
    domain_url = reverse("admin:core_domain_changelist")
    return {
        "client": reverse("admin:core_client_change", args=[client.pk]),
        "password": reverse("admin:core_client_change_password", args=[client.pk]),
        "domains": f"{domain_url}?q={client.schema_name}",
        "workspace_access": f"{access_url}?q={domain}" if domain else access_url,
    }


def _tenant_detail_data(client):
    data = {
        "company": None,
        "company_files": [],
        "company_files_count": 0,
        "current_dna": None,
        "complete_dna": None,
        "dna_versions": [],
        "products": [],
        "products_count": 0,
        "product_files_count": 0,
        "sources": [],
        "pipeline_runs": [],
        "llm_calls": [],
        "llm_cost_month": 0.0,
        "warning": "",
    }
    try:
        with _tenant_context(client.schema_name):
            company = Company.objects.filter(schema_name=client.schema_name).first()
            if not company:
                data["warning"] = "Company non ancora creata nel tenant."
                return data

            data["company"] = company
            data["company_files"] = list(company.company_files.select_related("uploaded_by")[:20])
            for company_file in data["company_files"]:
                company_file.open_url = reverse(
                    "zeus-admin-company-file-open",
                    args=[client.pk, company_file.pk],
                )
                company_file.delete_url = reverse(
                    "zeus-admin-company-file-delete",
                    args=[client.pk, company_file.pk],
                )
            data["company_files_count"] = CompanyFile.objects.filter(
                company=company,
            ).count()
            data["current_dna"] = company.dna_versions.filter(is_current=True).first()
            data["complete_dna"] = company.dna_versions.filter(
                dna_type=CompanyDNA.TYPE_COMPLETE,
                is_current=True,
            ).first()
            data["dna_versions"] = list(company.dna_versions.all()[:8])
            for dna in data["dna_versions"]:
                dna.open_url = reverse(
                    "zeus-admin-company-dna-open",
                    args=[client.pk, dna.pk],
                )
                dna.delete_url = reverse(
                    "zeus-admin-company-dna-delete",
                    args=[client.pk, dna.pk],
                )
            data["sources"] = list(company.sources.all()[:6])
            pipeline_runs = list(company.pipeline_runs.select_related("source")[:8])
            for run in pipeline_runs:
                run.tone = _pipeline_tone(run.status)
            data["pipeline_runs"] = pipeline_runs
            data["llm_calls"] = list(company.llm_calls.all()[:10])
            data["llm_cost_month"] = (
                company.llm_calls.filter(created_at__gte=_month_start()).aggregate(
                    total=Sum("cost_usd"),
                )["total"]
                or 0.0
            )

            products = []
            for product in company.products.prefetch_related(
                "product_files",
                "dna_versions",
            ):
                files = list(product.product_files.select_related("uploaded_by")[:10])
                for product_file in files:
                    product_file.open_url = reverse(
                        "zeus-admin-product-file-open",
                        args=[client.pk, product_file.pk],
                    )
                    product_file.delete_url = reverse(
                        "zeus-admin-product-file-delete",
                        args=[client.pk, product_file.pk],
                    )
                current_dna = product.dna_versions.filter(is_current=True).first()
                dna_versions = list(product.dna_versions.all()[:8])
                for product_dna in dna_versions:
                    product_dna.open_url = reverse(
                        "zeus-admin-product-dna-open",
                        args=[client.pk, product_dna.pk],
                    )
                    product_dna.delete_url = reverse(
                        "zeus-admin-product-dna-delete",
                        args=[client.pk, product_dna.pk],
                    )
                products.append(
                    {
                        "product": product,
                        "files": files,
                        "files_count": len(files),
                        "current_dna": current_dna,
                        "dna_versions": dna_versions,
                    }
                )
            data["products"] = products
            data["products_count"] = len(products)
            data["product_files_count"] = ProductFile.objects.filter(
                product__company=company,
            ).count()
    except Exception:
        data["warning"] = "Tenant non leggibile. Controllare schema e migrazioni."
    return data


def _update_client_config(request, client):
    plan = Plan.objects.filter(pk=request.POST.get("plan_id")).first() or Plan.get_default()
    status = request.POST.get("status", WorkspaceSubscription.STATUS_TRIAL)
    valid_statuses = {choice[0] for choice in WorkspaceSubscription.STATUS_CHOICES}
    if status not in valid_statuses:
        status = WorkspaceSubscription.STATUS_TRIAL

    paid_until_raw = request.POST.get("paid_until", "").strip()
    client.paid_until = parse_date(paid_until_raw) if paid_until_raw else None
    client.on_trial = request.POST.get("on_trial") == "on"
    client.save(update_fields=["paid_until", "on_trial"])

    subscription, _ = WorkspaceSubscription.objects.get_or_create(
        client=client,
        defaults={"plan": plan, "status": status},
    )
    subscription.plan = plan
    subscription.status = status
    subscription.notes = request.POST.get("notes", "").strip()
    subscription.save()


def _client_detail_context(request, client):
    domain = _primary_domain(client)
    subscription = getattr(client, "subscription", None)
    tenant_data = _tenant_detail_data(client)
    plans = list(Plan.objects.filter(is_active=True).order_by("name"))
    if not plans:
        plans = [Plan.get_default()]
    plan = subscription.plan if subscription else plans[0]
    company_files_used = tenant_data["company_files_count"]
    products_used = tenant_data["products_count"]
    return {
        "client": client,
        "domain": domain,
        "domains": list(client.domains.all()),
        "owner_email": _owner_email(domain),
        "workspace_url": f"https://{domain}/onboarding/" if domain else "",
        "admin_links": _client_admin_links(client, domain),
        "subscription": subscription,
        "plans": plans,
        "status_choices": WorkspaceSubscription.STATUS_CHOICES,
        "current_status": subscription.status
        if subscription
        else WorkspaceSubscription.STATUS_TRIAL,
        "current_plan_id": plan.pk,
        "tenant": tenant_data,
        "saved": request.GET.get("saved") == "1",
        "deleted": request.GET.get("deleted") == "1",
        "not_deleted": request.GET.get("not_deleted") == "1",
        "limits": {
            "company_files": {
                "used": company_files_used,
                "limit": _limit_label(
                    plan,
                    "max_company_files",
                    "unlimited_company_files",
                ),
            },
            "products": {
                "used": products_used,
                "limit": _limit_label(
                    plan,
                    "max_product_dnas",
                    "unlimited_product_dnas",
                ),
            },
            "files_per_product": _limit_label(
                plan,
                "max_files_per_product",
                "unlimited_files_per_product",
            ),
        },
    }


def _tenant_company_or_404(client):
    company = Company.objects.filter(schema_name=client.schema_name).first()
    if not company:
        raise Http404("Company not found")
    return company


def _client_detail_redirect(client, flag):
    return redirect(f"{reverse('zeus-admin-client-detail', args=[client.pk])}?{flag}=1")


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


@staff_member_required
def client_detail(request, client_id):
    client = get_object_or_404(_clients_queryset(), pk=client_id)
    if request.method == "POST":
        _update_client_config(request, client)
        return redirect(f"{reverse('zeus-admin-client-detail', args=[client.pk])}?saved=1")
    context = _client_detail_context(request, client)
    return render(request, "zeus_admin/client_detail.html", context)


@staff_member_required
def open_company_file(request, client_id, file_id):
    client = get_object_or_404(_clients_queryset(), pk=client_id)
    with _tenant_context(client.schema_name):
        company = _tenant_company_or_404(client)
        company_file = get_object_or_404(CompanyFile, pk=file_id, company=company)
        return _content_response(
            company_file.original_name,
            f"Allegato aziendale · {company_file.file_size} byte · "
            f"caricato {_admin_datetime(company_file.created_at)}",
            company_file.content_text,
        )


@staff_member_required
def delete_company_file(request, client_id, file_id):
    client = get_object_or_404(_clients_queryset(), pk=client_id)
    if request.method != "POST":
        return _client_detail_redirect(client, "not_deleted")
    current_count = 0
    with _tenant_context(client.schema_name):
        company = _tenant_company_or_404(client)
        company_file = get_object_or_404(CompanyFile, pk=file_id, company=company)
        company_file.delete()
        current_count = company.company_files.count()
    subscription = getattr(client, "subscription", None)
    if subscription:
        subscription.company_files_used = current_count
        subscription.save(update_fields=["company_files_used"])
    return _client_detail_redirect(client, "deleted")


@staff_member_required
def open_product_file(request, client_id, file_id):
    client = get_object_or_404(_clients_queryset(), pk=client_id)
    with _tenant_context(client.schema_name):
        company = _tenant_company_or_404(client)
        product_file = get_object_or_404(
            ProductFile,
            pk=file_id,
            product__company=company,
        )
        return _content_response(
            product_file.original_name,
            f"Allegato prodotto · {product_file.product.name} · "
            f"{product_file.file_size} byte · caricato {_admin_datetime(product_file.created_at)}",
            product_file.content_text,
        )


@staff_member_required
def delete_product_file(request, client_id, file_id):
    client = get_object_or_404(_clients_queryset(), pk=client_id)
    if request.method != "POST":
        return _client_detail_redirect(client, "not_deleted")
    with _tenant_context(client.schema_name):
        company = _tenant_company_or_404(client)
        product_file = get_object_or_404(
            ProductFile,
            pk=file_id,
            product__company=company,
        )
        product_file.delete()
    return _client_detail_redirect(client, "deleted")


@staff_member_required
def open_company_dna(request, client_id, dna_id):
    client = get_object_or_404(_clients_queryset(), pk=client_id)
    with _tenant_context(client.schema_name):
        company = _tenant_company_or_404(client)
        dna = get_object_or_404(CompanyDNA, pk=dna_id, company=company)
        status = "Approvato" if dna.is_fully_approved() else "Review"
        return _dna_response(
            f"{dna.get_dna_type_display()} v{dna.version}",
            f"DNA aziendale · {status} · creato {_admin_datetime(dna.created_at)}",
            dna,
        )


@staff_member_required
def delete_company_dna(request, client_id, dna_id):
    client = get_object_or_404(_clients_queryset(), pk=client_id)
    if request.method != "POST":
        return _client_detail_redirect(client, "not_deleted")
    with _tenant_context(client.schema_name):
        company = _tenant_company_or_404(client)
        dna = get_object_or_404(CompanyDNA, pk=dna_id, company=company)
        was_current = dna.is_current
        dna.delete()
        if was_current and not company.dna_versions.filter(is_current=True).exists():
            replacement = company.dna_versions.order_by("-version").first()
            if replacement:
                replacement.is_current = True
                replacement.save(update_fields=["is_current"])
    return _client_detail_redirect(client, "deleted")


@staff_member_required
def open_product_dna(request, client_id, dna_id):
    client = get_object_or_404(_clients_queryset(), pk=client_id)
    with _tenant_context(client.schema_name):
        company = _tenant_company_or_404(client)
        dna = get_object_or_404(ProductDNA, pk=dna_id, product__company=company)
        status = "Approvato" if dna.is_fully_approved() else "Review"
        return _dna_response(
            f"{dna.product.name} · {dna.get_dna_type_display()} v{dna.version}",
            f"ProductDNA · {status} · creato {_admin_datetime(dna.created_at)}",
            dna,
        )


@staff_member_required
def delete_product_dna(request, client_id, dna_id):
    client = get_object_or_404(_clients_queryset(), pk=client_id)
    if request.method != "POST":
        return _client_detail_redirect(client, "not_deleted")
    with _tenant_context(client.schema_name):
        company = _tenant_company_or_404(client)
        dna = get_object_or_404(ProductDNA, pk=dna_id, product__company=company)
        product = dna.product
        was_current = dna.is_current
        dna.delete()
        if was_current and not product.dna_versions.filter(is_current=True).exists():
            replacement = product.dna_versions.order_by("-version").first()
            if replacement:
                replacement.is_current = True
                replacement.save(update_fields=["is_current"])
    return _client_detail_redirect(client, "deleted")
