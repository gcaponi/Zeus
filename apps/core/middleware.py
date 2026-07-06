import logging
import time
import uuid

try:
    import structlog
except ImportError:  # pragma: no cover - production dependency, fallback for bare shells
    structlog = None

logger = structlog.get_logger(__name__) if structlog else logging.getLogger(__name__)


def _log(level, event, **fields):
    try:
        getattr(logger, level)(event, **fields)
    except TypeError:
        getattr(logger, level)("%s %s", event, fields)


def _bind_context(**fields):
    if structlog:
        structlog.contextvars.bind_contextvars(**fields)


def _clear_context():
    if structlog:
        structlog.contextvars.clear_contextvars()


def _set_sentry_context(request, request_id, tenant_id, latency_ms=None):
    try:
        import sentry_sdk
    except ImportError:
        return

    sentry_sdk.set_tag("request_id", request_id)
    sentry_sdk.set_tag("tenant_id", tenant_id or "")
    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False):
        sentry_sdk.set_user({"id": str(user.pk), "email": getattr(user, "email", "")})
        user_id = user.pk
    else:
        sentry_sdk.set_user(None)
        user_id = None
    sentry_sdk.set_context(
        "zeus_request",
        {
            "request_id": request_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "method": request.method,
            "path": request.path,
            "latency_ms": latency_ms,
        },
    )


def _capture_exception(exc):
    try:
        import sentry_sdk
    except ImportError:
        return
    sentry_sdk.capture_exception(exc)


class RequestContextLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.request_id = request_id
        tenant = getattr(request, "tenant", None)
        tenant_id = getattr(tenant, "schema_name", None)
        user = getattr(request, "user", None)
        user_id = getattr(user, "pk", None) if getattr(user, "is_authenticated", False) else None
        start = time.monotonic()

        _bind_context(request_id=request_id, tenant_id=tenant_id, user_id=user_id)
        _set_sentry_context(request, request_id, tenant_id)
        try:
            response = self.get_response(request)
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            _set_sentry_context(request, request_id, tenant_id, latency_ms)
            _capture_exception(exc)
            _log(
                "exception",
                "request_failed",
                request_id=request_id,
                tenant_id=tenant_id,
                user_id=user_id,
                method=request.method,
                path=request.path,
                status_code=500,
                latency_ms=latency_ms,
            )
            _clear_context()
            raise

        latency_ms = int((time.monotonic() - start) * 1000)
        response["X-Request-ID"] = request_id
        _log(
            "info",
            "request",
            request_id=request_id,
            tenant_id=tenant_id,
            user_id=user_id,
            method=request.method,
            path=request.path,
            status_code=response.status_code,
            latency_ms=latency_ms,
        )
        _clear_context()
        return response
