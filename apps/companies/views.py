import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from apps.companies.models import Company, CompanyDNA, DNAFeedback, PipelineRun, Source
from apps.companies.tasks import _generate_dna, run_pipeline, scrape_source

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

    dna, llm_call = _generate_dna(source, company)

    return JsonResponse({
        "dna_id": dna.id,
        "version": dna.version,
        "content": dna.content,
        "llm_call_id": llm_call.id,
        "tokens_in": llm_call.tokens_in,
        "tokens_out": llm_call.tokens_out,
        "cost_usd": llm_call.cost_usd,
    }, status=201)


@login_required
@require_http_methods(["POST"])
def pipeline_run_create(request):
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

    run = PipelineRun.objects.create(
        company=company,
        source=source,
        status=PipelineRun.STATUS_PENDING,
    )
    run_pipeline.delay(run.id)

    return JsonResponse({
        "id": run.id,
        "status": run.status,
        "current_step": run.current_step,
    }, status=201)


@login_required
@require_http_methods(["POST"])
def dna_feedback(request, pk):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)
    dna = CompanyDNA.objects.filter(
        pk=pk, company__schema_name=tenant.schema_name,
    ).first()
    if not dna:
        return JsonResponse({"error": "dna not found"}, status=404)

    body = json.loads(request.body)
    rating = body.get("rating")
    if not rating or not isinstance(rating, int) or rating < 1 or rating > 5:
        return JsonResponse({"error": "rating must be 1-5"}, status=400)

    feedback = DNAFeedback.objects.create(
        dna=dna,
        rating=rating,
        comment=body.get("comment", ""),
    )
    dna.confidence_score = CompanyDNA.recalculate_confidence(dna.id)
    dna.save(update_fields=["confidence_score"])

    return JsonResponse({
        "id": feedback.id,
        "rating": feedback.rating,
        "comment": feedback.comment,
        "confidence_score": dna.confidence_score,
    }, status=201)


@login_required
def pipeline_run_detail(request, pk):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return JsonResponse({"error": "no tenant"}, status=400)
    run = PipelineRun.objects.filter(
        pk=pk, company__schema_name=tenant.schema_name,
    ).first()
    if not run:
        return JsonResponse({"error": "not found"}, status=404)
    return JsonResponse({
        "id": run.id,
        "status": run.status,
        "current_step": run.current_step,
        "error_msg": run.error_msg,
        "created_at": run.created_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    })


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
