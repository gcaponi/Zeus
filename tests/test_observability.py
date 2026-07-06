from datetime import timedelta

import pytest
from django.http import HttpResponse
from django.test import Client as DjangoClient
from django.test import RequestFactory
from django.utils import timezone

from apps.companies.models import Company, LLMCall, PipelineRun
from apps.core import middleware
from apps.core.middleware import RequestContextLoggingMiddleware


@pytest.mark.django_db
class TestMetricsEndpoint:
    def test_metrics_endpoint_returns_prometheus_format(self):
        company = Company.objects.create(schema_name="metrics", name="Metrics")
        now = timezone.now()
        completed = PipelineRun.objects.create(
            company=company,
            status=PipelineRun.STATUS_COMPLETED,
            completed_at=now,
        )
        PipelineRun.objects.filter(pk=completed.pk).update(
            created_at=now - timedelta(seconds=5),
            completed_at=now,
        )
        PipelineRun.objects.create(company=company, status=PipelineRun.STATUS_FAILED)
        LLMCall.objects.create(
            company=company,
            model_name="deepseek-chat",
            prompt_text="prompt",
            response_text="response",
            tokens_in=10,
            tokens_out=20,
            cost_usd=0.25,
            latency_ms=100,
        )

        response = DjangoClient().get("/metrics/")

        body = response.content.decode()
        assert response.status_code == 200
        assert "text/plain" in response["Content-Type"]
        assert 'zeus_pipeline_runs_total{status="completed"} 1' in body
        assert 'zeus_pipeline_runs_total{status="failed"} 1' in body
        assert 'zeus_llm_cost_usd_total{model="deepseek-chat"} 0.250000' in body
        assert "zeus_dna_generation_seconds_bucket" in body
        assert "zeus_dna_generation_seconds_count 1" in body


class TestRequestContextLoggingMiddleware:
    def test_logs_one_entry_per_request_with_consistent_request_id(self, monkeypatch):
        entries = []

        class FakeLogger:
            def info(self, event, **fields):
                entries.append((event, fields))

            def exception(self, event, **fields):
                entries.append((event, fields))

        monkeypatch.setattr(middleware, "logger", FakeLogger())
        rf = RequestFactory()
        wrapped = RequestContextLoggingMiddleware(lambda _request: HttpResponse("ok"))

        for _ in range(5):
            response = wrapped(rf.get("/health/", HTTP_X_REQUEST_ID="req-123"))
            assert response["X-Request-ID"] == "req-123"

        assert len(entries) == 5
        assert all(event == "request" for event, _fields in entries)
        assert all(fields["request_id"] == "req-123" for _event, fields in entries)

    def test_captures_exception_with_request_context(self, monkeypatch):
        captured = []
        entries = []

        class FakeLogger:
            def info(self, event, **fields):
                entries.append((event, fields))

            def exception(self, event, **fields):
                entries.append((event, fields))

        def raise_error(_request):
            raise RuntimeError("boom")

        monkeypatch.setattr(middleware, "logger", FakeLogger())
        monkeypatch.setattr(middleware, "_capture_exception", lambda exc: captured.append(exc))
        wrapped = RequestContextLoggingMiddleware(raise_error)

        with pytest.raises(RuntimeError):
            wrapped(RequestFactory().get("/boom/", HTTP_X_REQUEST_ID="req-err"))

        assert isinstance(captured[0], RuntimeError)
        assert entries[0][0] == "request_failed"
        assert entries[0][1]["request_id"] == "req-err"
        assert entries[0][1]["status_code"] == 500
