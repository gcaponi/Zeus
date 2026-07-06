from collections import defaultdict
from contextlib import nullcontext

from django.conf import settings
from django.db.models import Sum
from django.http import HttpResponse

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
DNA_GENERATION_BUCKETS = (1, 5, 10, 30, 60, 120, 300, 600)


def _escape_label(value) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _sample(name, value, labels=None):
    labels = labels or {}
    if labels:
        label_text = ",".join(
            f'{key}="{_escape_label(val)}"' for key, val in sorted(labels.items())
        )
        return f"{name}{{{label_text}}} {value}"
    return f"{name} {value}"


def _tenant_contexts():
    engine = settings.DATABASES["default"]["ENGINE"]
    if "django_tenants" not in engine:
        yield nullcontext()
        return

    from django_tenants.utils import schema_context

    from apps.core.models import Client

    for schema_name in Client.objects.values_list("schema_name", flat=True):
        yield schema_context(schema_name)


def _collect_metrics():
    from apps.companies.models import LLMCall, PipelineRun

    pipeline_totals = {
        PipelineRun.STATUS_COMPLETED: 0,
        PipelineRun.STATUS_FAILED: 0,
    }
    llm_costs = defaultdict(float)
    durations = []
    errors = []

    for context in _tenant_contexts():
        try:
            with context:
                for status in pipeline_totals:
                    pipeline_totals[status] += PipelineRun.objects.filter(status=status).count()
                for row in LLMCall.objects.values("model_name").annotate(total=Sum("cost_usd")):
                    llm_costs[row["model_name"]] += float(row["total"] or 0)
                completed_runs = PipelineRun.objects.filter(
                    status=PipelineRun.STATUS_COMPLETED,
                    completed_at__isnull=False,
                ).values_list("created_at", "completed_at")
                for created_at, completed_at in completed_runs:
                    duration = (completed_at - created_at).total_seconds()
                    if duration >= 0:
                        durations.append(duration)
        # Protect public schema during partial tenant deploys.
        except Exception as exc:  # pragma: no cover
            errors.append(exc.__class__.__name__)

    return pipeline_totals, llm_costs, durations, errors


def prometheus_metrics_text():
    pipeline_totals, llm_costs, durations, errors = _collect_metrics()
    lines = [
        "# HELP zeus_pipeline_runs_total Pipeline runs by terminal status.",
        "# TYPE zeus_pipeline_runs_total counter",
    ]
    for status, total in sorted(pipeline_totals.items()):
        lines.append(_sample("zeus_pipeline_runs_total", total, {"status": status}))

    lines.extend(
        [
            "# HELP zeus_llm_cost_usd_total Total LLM cost in USD by model.",
            "# TYPE zeus_llm_cost_usd_total counter",
        ]
    )
    for model, total in sorted(llm_costs.items()):
        lines.append(_sample("zeus_llm_cost_usd_total", f"{total:.6f}", {"model": model}))
    if not llm_costs:
        lines.append(_sample("zeus_llm_cost_usd_total", "0.000000", {"model": "unknown"}))

    lines.extend(
        [
            "# HELP zeus_dna_generation_seconds DNA pipeline duration in seconds.",
            "# TYPE zeus_dna_generation_seconds histogram",
        ]
    )
    cumulative = 0
    sorted_durations = sorted(durations)
    for bucket in DNA_GENERATION_BUCKETS:
        cumulative = sum(1 for duration in sorted_durations if duration <= bucket)
        lines.append(_sample("zeus_dna_generation_seconds_bucket", cumulative, {"le": bucket}))
    lines.append(_sample("zeus_dna_generation_seconds_bucket", len(durations), {"le": "+Inf"}))
    lines.append(_sample("zeus_dna_generation_seconds_count", len(durations)))
    lines.append(_sample("zeus_dna_generation_seconds_sum", f"{sum(durations):.6f}"))

    if errors:
        lines.append(f"# scrape_errors {','.join(sorted(set(errors)))}")
    return "\n".join(lines) + "\n"


def metrics_view(_request):
    return HttpResponse(prometheus_metrics_text(), content_type=PROMETHEUS_CONTENT_TYPE)
