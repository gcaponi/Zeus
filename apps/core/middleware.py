import logging
import time
import uuid
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.sessions.backends.base import UpdateError
from django.contrib.sessions.exceptions import SessionInterrupted
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils.cache import patch_vary_headers
from django.utils.http import http_date

try:
    import structlog
except ImportError:  # pragma: no cover - production dependency, fallback for bare shells
    structlog = None

logger = structlog.get_logger(__name__) if structlog else logging.getLogger(__name__)


ANAGRAFICA_ALLOWED_PATHS = (
    "/company/anagrafica/",
    "/accounts/",
    "/admin/",
    "/zeus-admin/",
    "/health/",
    "/metrics/",
    "/static/",
    "/__shell_preview/",
    "/guide/",
)


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


class CompanyAnagraficaRequiredMiddleware:
    """Gate authenticated tenant traffic until company identity is complete."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant = getattr(request, "tenant", None)
        user = getattr(request, "user", None)
        is_tenant = tenant is not None and getattr(tenant, "schema_name", "public") != "public"
        is_authenticated = bool(user and getattr(user, "is_authenticated", False))
        is_allowed_path = request.path.startswith(ANAGRAFICA_ALLOWED_PATHS)

        if is_tenant and is_authenticated and not is_allowed_path:
            from apps.companies.models import Company

            company = Company.objects.filter(schema_name=tenant.schema_name).first()
            if company is None or not company.has_complete_anagrafica():
                target = "/company/anagrafica/"
                if request.path.startswith("/api/") or "application/json" in request.headers.get(
                    "Accept", ""
                ):
                    return JsonResponse(
                        {
                            "error": "company_profile_required",
                            "anagrafica_url": target,
                        },
                        status=409,
                    )
                query = urlencode({"next": request.get_full_path()})
                return redirect(f"{target}?{query}")

        return self.get_response(request)


# --- Sessioni separate per la zona admin (fix logout incrociato app/admin) ---
#
# La sessione Django è unica per tutto il dominio .zeus.cais.uno (tabella
# django_session nel public schema). Il login admin (schema public) e il login
# app (schema tenant) condividono lo stesso cookie: la rotazione della chiave
# al login e l'utente di un altro schema (pk collision + session auth hash
# mismatch) invalidano la sessione dell'altra zona, con logout a cascata.
# Soluzione: cookie di sessione dedicato per /admin/ e /zeus-admin/.

ADMIN_SESSION_PATHS = ("/admin/", "/zeus-admin/")


def _session_cookie_name(request):
    """Cookie di sessione dedicato per la zona admin, quello standard altrove."""
    if request.path.startswith(ADMIN_SESSION_PATHS):
        return settings.ADMIN_SESSION_COOKIE_NAME
    return settings.SESSION_COOKIE_NAME


def _session_cookie_domain(request):
    """Dominio del cookie di sessione: la zona admin e' HOST-ONLY.

    Con un Domain condiviso (.zeus.cais.uno) il browser invierebbe il cookie
    admin a OGNI tenant sibling: un tenant ostile potrebbe catturare la
    sessione del control-plane. Nessun attributo Domain = il browser lo
    limita all'host che lo ha emesso (Codex Security finding 1).
    """
    if request.path.startswith(ADMIN_SESSION_PATHS):
        return None
    return settings.SESSION_COOKIE_DOMAIN


class TenantAwareSessionMiddleware(SessionMiddleware):
    """SessionMiddleware con cookie di sessione separato per la zona admin.

    La zona admin (/admin/, /zeus-admin/) usa `ADMIN_SESSION_COOKIE_NAME`:
    il login admin non ruota più la sessione dell'app e viceversa.
    """

    def process_request(self, request):
        session_key = request.COOKIES.get(_session_cookie_name(request))
        request.session = self.SessionStore(session_key)

    def process_response(self, request, response):
        cookie_name = _session_cookie_name(request)
        try:
            accessed = request.session.accessed
            modified = request.session.modified
            empty = request.session.is_empty()
        except AttributeError:
            return response
        # La sessione va cancellata solo se completamente vuota.
        if cookie_name in request.COOKIES and empty:
            response.delete_cookie(
                cookie_name,
                path=settings.SESSION_COOKIE_PATH,
                domain=_session_cookie_domain(request),
                samesite=settings.SESSION_COOKIE_SAMESITE,
            )
            patch_vary_headers(response, ("Cookie",))
        else:
            if accessed:
                patch_vary_headers(response, ("Cookie",))
            if (modified or settings.SESSION_SAVE_EVERY_REQUEST) and not empty:
                if request.session.get_expire_at_browser_close():
                    max_age = None
                    expires = None
                else:
                    max_age = request.session.get_expiry_age()
                    expires_time = time.time() + max_age
                    expires = http_date(expires_time)
                # Salva i dati di sessione e aggiorna il cookie client.
                # Salvataggio saltato per le risposte 5xx.
                if response.status_code < 500:
                    try:
                        request.session.save()
                    except UpdateError:
                        raise SessionInterrupted(
                            "The request's session was deleted before the "
                            "request completed. The user may have logged "
                            "out in a concurrent request, for example."
                        ) from None
                    response.set_cookie(
                        cookie_name,
                        request.session.session_key,
                        max_age=max_age,
                        expires=expires,
                        domain=_session_cookie_domain(request),
                        path=settings.SESSION_COOKIE_PATH,
                        secure=settings.SESSION_COOKIE_SECURE or None,
                        httponly=settings.SESSION_COOKIE_HTTPONLY or None,
                        samesite=settings.SESSION_COOKIE_SAMESITE,
                    )
        return response
