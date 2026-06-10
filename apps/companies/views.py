import json
import logging
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from apps.companies.llm_client import LLM_MODEL, get_llm_client
from apps.companies.models import Company, CompanyDNA, LLMCall, Source
from apps.companies.tasks import scrape_source

logger = logging.getLogger(__name__)


@login_required
def company_detail(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)
    company, created = Company.objects.get_or_create(
        schema_name=tenant.schema_name,
        defaults={"name": tenant.name},
    )
    return JsonResponse({
        "id": company.id,
        "name": company.name,
        "created_at": company.created_at.isoformat(),
    })


@login_required
def dna_current(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)
    company = Company.objects.filter(schema_name=tenant.schema_name).first()
    if not company:
        return JsonResponse({"error": "company not found"}, status=404)
    dna = company.dna_versions.filter(is_current=True).first()
    if not dna:
        return JsonResponse({"error": "no DNA yet"}, status=404)
    return JsonResponse({
        "id": dna.id,
        "version": dna.version,
        "content": dna.content,
        "created_at": dna.created_at.isoformat(),
        "created_by": dna.created_by.email if dna.created_by else None,
    })


@login_required
def dna_history(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)
    company = Company.objects.filter(schema_name=tenant.schema_name).first()
    if not company:
        return JsonResponse({"error": "company not found"}, status=404)
    versions = company.dna_versions.all().values(
        "id", "version", "is_current", "created_at", "created_by__email"
    )
    return JsonResponse(list(versions), safe=False)


@login_required
@require_http_methods(["GET", "POST"])
def source_list_create(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)

    company, _ = Company.objects.get_or_create(
        schema_name=tenant.schema_name,
        defaults={"name": tenant.name},
    )

    if request.method == "GET":
        sources = company.sources.all().values(
            "id", "url", "status", "created_at", "updated_at"
        )
        return JsonResponse(list(sources), safe=False)

    body = json.loads(request.body)
    url = body.get("url")
    if not url:
        return JsonResponse({"error": "url is required"}, status=400)

    source = Source.objects.create(
        company=company,
        url=url,
        status=Source.STATUS_PENDING,
    )

    scrape_source.delay(source.id)

    return JsonResponse({
        "id": source.id,
        "url": source.url,
        "status": source.status,
    }, status=201)


@login_required
def source_detail(request, pk):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)

    source = Source.objects.filter(pk=pk, company__schema_name=tenant.schema_name).first()
    if not source:
        return JsonResponse({"error": "not found"}, status=404)

    return JsonResponse({
        "id": source.id,
        "url": source.url,
        "status": source.status,
        "scraped_data": source.scraped_data,
        "error_msg": source.error_msg,
        "created_at": source.created_at.isoformat(),
        "updated_at": source.updated_at.isoformat(),
    })


@login_required
@require_http_methods(["POST"])
def dna_generate(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)
    company, _ = Company.objects.get_or_create(
        schema_name=tenant.schema_name,
        defaults={"name": tenant.name},
    )
    body = json.loads(request.body)
    source_id = body.get("source_id")
    if not source_id:
        return JsonResponse({"error": "source_id is required"}, status=400)
    source = Source.objects.filter(pk=source_id, company=company).first()
    if not source:
        return JsonResponse({"error": "source not found"}, status=404)
    if source.status != Source.STATUS_SCRAPED or not source.scraped_data:
        return JsonResponse({"error": "source not scraped yet"}, status=400)

    prompt_path = Path(__file__).parent / "prompts" / "dna_aziendale_v0.1.md"
    prompt_template = prompt_path.read_text(encoding="utf-8")
    prompt = prompt_template.replace("{{scraped_content}}", source.scraped_data.get("markdown", ""))

    client = get_llm_client()
    result = client.generate(prompt)

    llm_call = LLMCall.objects.create(
        company=company,
        model_name=LLM_MODEL,
        prompt_text=prompt,
        response_text=result.text,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost,
        latency_ms=result.latency_ms,
        source=source,
    )

    last_version = company.dna_versions.order_by("-version").first()
    next_version = (last_version.version + 1) if last_version else 1
    try:
        content = json.loads(result.text)
    except json.JSONDecodeError:
        content = {"raw": result.text}

    dna = CompanyDNA.objects.create(
        company=company,
        version=next_version,
        content=content,
        created_by=request.user if request.user.is_authenticated else None,
    )

    return JsonResponse({
        "dna_id": dna.id,
        "version": dna.version,
        "content": dna.content,
        "llm_call_id": llm_call.id,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "cost_usd": result.cost,
    }, status=201)


@login_required
@require_http_methods(["POST"])
def dna_create(request):

    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)
    company, _ = Company.objects.get_or_create(
        schema_name=tenant.schema_name,
        defaults={"name": tenant.name},
    )
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "invalid JSON"}, status=400)

    content = body.get("content")
    if not content:
        return JsonResponse({"error": "content is required"}, status=400)

    last_version = company.dna_versions.order_by("-version").first()
    next_version = (last_version.version + 1) if last_version else 1

    # mark previous current as False
    company.dna_versions.filter(is_current=True).update(is_current=False)

    dna = CompanyDNA.objects.create(
        company=company,
        version=next_version,
        content=content,
        created_by=request.user if request.user.is_authenticated else None,
    )
    return JsonResponse({
        "id": dna.id,
        "version": dna.version,
        "content": dna.content,
        "created_at": dna.created_at.isoformat(),
    }, status=201)
