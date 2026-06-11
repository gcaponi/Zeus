import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.companies.models import (
    Company,
    CompanyDNA,
    DNAFeedback,
    PipelineRun,
    SectionApproval,
    Source,
)
from apps.companies.tasks import _generate_dna, run_pipeline, scrape_source

logger = logging.getLogger(__name__)


def _tenant_company(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return None
    company, _ = Company.objects.get_or_create(
        schema_name=tenant.schema_name,
        defaults={"name": tenant.name},
    )
    return company


def _onboarding_context(request):
    company = _tenant_company(request)
    if not company:
        return None
    latest_source = company.sources.order_by("-created_at").first()
    latest_run = company.pipeline_runs.select_related("source").order_by("-created_at").first()
    latest_dna = company.dna_versions.filter(is_current=True).order_by("-version").first()
    return {
        "company": company,
        "source": latest_source,
        "run": latest_run,
        "dna": latest_dna,
        "is_done": latest_dna is not None,
    }


def _dna_sections(content, old_content=None):
    labels = {
        "chi_siamo": "Chi siamo",
        "mission": "Mission",
        "settore": "Settore",
        "mercato": "Mercato",
        "pilastri": "Pilastri",
    }
    sections = []
    for key, label in labels.items():
        value = content.get(key) if isinstance(content, dict) else None
        if isinstance(value, list):
            value = ", ".join(map(str, value))
        old_value = None
        if old_content and isinstance(old_content, dict):
            old_value = old_content.get(key)
            if isinstance(old_value, list):
                old_value = ", ".join(map(str, old_value))
        sections.append({
            "key": key,
            "label": label,
            "value": value or "",
            "old_value": old_value or "",
            "changed": bool(old_value and old_value != value),
        })
    return sections


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
def onboarding_index(request):
    context = _onboarding_context(request)
    if not context:
        return HttpResponse("No tenant", status=400)
    return render(request, "core/onboarding.html", context)


@login_required
@require_http_methods(["POST"])
def onboarding_source_create(request):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)

    url = request.POST.get("url", "").strip()
    if not url:
        return render(request, "core/onboarding/_source_form.html", {
            "error": "Inserisci un URL valido.",
        }, status=400)

    source = Source.objects.create(company=company, url=url, status=Source.STATUS_PENDING)
    run = PipelineRun.objects.create(
        company=company,
        source=source,
        status=PipelineRun.STATUS_PENDING,
    )
    run_pipeline.delay(run.id)
    run.refresh_from_db()
    source.refresh_from_db()

    dna = company.dna_versions.filter(is_current=True).order_by("-version").first()
    if run.status == PipelineRun.STATUS_COMPLETED and dna:
        return render(request, "core/onboarding/_dna.html", {
            "dna": dna,
            "sections": _dna_sections(dna.content),
        })

    return render(request, "core/onboarding/_progress.html", {
        "run": run,
        "source": source,
    })


@login_required
def onboarding_status(request, pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    run = PipelineRun.objects.filter(
        pk=pk, company=company,
    ).select_related("source").first()
    if not run:
        return JsonResponse({"error": "not found"}, status=404)
    dna = company.dna_versions.filter(is_current=True).order_by("-version").first()
    return JsonResponse({
        "id": run.id,
        "status": run.status,
        "current_step": run.current_step,
        "error_msg": run.error_msg,
        "source_status": run.source.status if run.source else None,
        "dna_id": dna.id if dna else None,
    })


@login_required
def onboarding_dna(request, pk):
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    dna = CompanyDNA.objects.filter(
        pk=pk, company=company, is_current=True,
    ).first()
    if not dna:
        return HttpResponse("DNA not found", status=404)
    return render(request, "core/onboarding/_dna.html", {
        "dna": dna,
        "sections": _dna_sections(dna.content),
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


@login_required
def dna_review(request):
    """Pagina review DNA — mostra sezioni con stato approvazione."""
    company = _tenant_company(request)
    if not company:
        return HttpResponse("No tenant", status=400)
    dna = company.dna_versions.filter(is_current=True).first()
    if not dna:
        return HttpResponse("DNA not found", status=404)
    return render(request, "core/dna_review.html", {
        "dna": dna,
        "sections": _dna_sections(dna.content),
        "approved_keys": dna.approved_sections(),
        "missing_keys": dna.missing_sections(),
        "is_fully_approved": dna.is_fully_approved(),
    })


@login_required
@require_http_methods(["POST"])
def dna_section_approve(request, pk, section_key):
    """Approva una sezione specifica del DNA."""
    company = _tenant_company(request)
    if not company:
        return JsonResponse({"error": "no tenant"}, status=400)
    dna = CompanyDNA.objects.filter(pk=pk, company=company, is_current=True).first()
    if not dna:
        return JsonResponse({"error": "dna not found"}, status=404)
    if section_key not in {"chi_siamo", "mission", "settore", "mercato", "pilastri"}:
        return JsonResponse({"error": "invalid section_key"}, status=400)

    body = json.loads(request.body)
    comment = body.get("comment", "")
    is_clarification = body.get("is_clarification", False)

    SectionApproval.objects.update_or_create(
        dna=dna,
        section_key=section_key,
        defaults={
            "approved_by": request.user,
            "comment": comment,
            "is_clarification": is_clarification,
        },
    )

    # Check if all sections approved
    if not dna.missing_sections():
        dna.is_approved = timezone.now()
        dna.save(update_fields=["is_approved"])

    return JsonResponse({
        "section_key": section_key,
        "approved": True,
        "is_fully_approved": dna.is_fully_approved(),
        "missing_sections": dna.missing_sections(),
    })


@login_required
@require_http_methods(["POST"])
def dna_section_edit(request, pk, section_key):
    """Modifica una sezione → nuovo DNA v+1 con approvazioni trasferite (opzione B)."""
    company = _tenant_company(request)
    if not company:
        return JsonResponse({"error": "no tenant"}, status=400)
    old_dna = CompanyDNA.objects.filter(pk=pk, company=company, is_current=True).first()
    if not old_dna:
        return JsonResponse({"error": "dna not found"}, status=404)
    if section_key not in {"chi_siamo", "mission", "settore", "mercato", "pilastri"}:
        return JsonResponse({"error": "invalid section_key"}, status=400)

    body = json.loads(request.body)
    new_text = body.get("text", "").strip()
    if not new_text:
        return JsonResponse({"error": "text is required"}, status=400)

    # Build new content with modified section
    content = dict(old_dna.content) if isinstance(old_dna.content, dict) else {}
    content[section_key] = new_text

    # Mark old current as False
    company.dna_versions.filter(is_current=True).update(is_current=False)

    # Create new DNA v+1
    new_dna = CompanyDNA.objects.create(
        company=company,
        version=old_dna.version + 1,
        content=content,
        created_by=request.user,
    )

    # Transfer approvals from old DNA to new DNA (Option B)
    for approval in old_dna.section_approvals.all():
        if approval.section_key != section_key:
            SectionApproval.objects.create(
                dna=new_dna,
                section_key=approval.section_key,
                approved_by=approval.approved_by,
                comment=approval.comment,
                is_clarification=approval.is_clarification,
            )

    return JsonResponse({
        "dna_id": new_dna.id,
        "version": new_dna.version,
        "section_key": section_key,
        "transferred_approvals": [
            {
                "section_key": a.section_key,
                "approved_by": a.approved_by.email if a.approved_by else None,
            }
            for a in new_dna.section_approvals.all()
        ],
        "missing_sections": new_dna.missing_sections(),
    })
