import hashlib
import ipaddress
import logging
import secrets
from datetime import timedelta
from importlib import import_module

from allauth.account.views import SignupView
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.core import signing
from django.core.cache import cache
from django.core.mail import send_mail
from django.core.signing import BadSignature, SignatureExpired
from django.db import IntegrityError, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET
from django_tenants.utils import schema_context

from apps.core.forms import ZEUSSignupForm
from apps.core.models import (
    Client,
    Domain,
    LoginHandoff,
    Plan,
    SignupProvisioning,
    WorkspaceAccess,
    WorkspaceSubscription,
)

WORKSPACE_COOKIE = "zeus_workspace"
WORKSPACE_COOKIE_MAX_AGE = 60 * 60 * 24 * 30

LOGIN_HANDOFF_TTL_SECONDS = 60
LOGIN_HANDOFF_SALT = "apps.core.login-handoff"
SIGNUP_VERIFY_SALT = "apps.core.signup-verify"
logger = logging.getLogger(__name__)


def health_check(request):
    return JsonResponse({"status": "ok"})


def app_shell_preview(request):
    if not settings.ZEUS_APP_SHELL_ENABLED:
        raise Http404
    return render(request, "core/app_shell_preview.html")


def _set_workspace_cookie(response, workspace):
    response.set_cookie(
        WORKSPACE_COOKIE,
        workspace,
        max_age=WORKSPACE_COOKIE_MAX_AGE,
        samesite="Lax",
    )
    return response


def _clear_workspace_cookie(response):
    response.delete_cookie(WORKSPACE_COOKIE)
    response.delete_cookie(WORKSPACE_COOKIE, domain=".zeus.cais.uno")
    return response


def _valid_workspace_cookie(request):
    workspace = request.COOKIES.get(WORKSPACE_COOKIE, "").strip().lower()
    if not workspace:
        return None
    if "/" in workspace or not workspace.endswith(".zeus.cais.uno"):
        return None
    if not Domain.objects.filter(domain=workspace).exists():
        return None
    return workspace


def redirect_to_workspace_or_login(request):
    workspace = _valid_workspace_cookie(request)
    if workspace:
        return redirect(f"https://{workspace}/onboarding/")
    response = redirect("https://zeus.cais.uno/accounts/login/")
    return _clear_workspace_cookie(response)


def _create_login_handoff(tenant_schema, user):
    """Token monouso (60s) per aprire la sessione sull'host del tenant.

    In tabella solo l'hash SHA-256 del token; pulizia opportunistica dei
    token scaduti a ogni emissione (tabella minuscola, una riga per login).
    """
    nonce = secrets.token_urlsafe(32)
    now = timezone.now()
    LoginHandoff.objects.create(
        token_hash=hashlib.sha256(nonce.encode()).hexdigest(),
        tenant_schema=tenant_schema,
        user_id=user.pk,
        expires_at=now + timedelta(seconds=LOGIN_HANDOFF_TTL_SECONDS),
    )
    LoginHandoff.objects.filter(expires_at__lt=now).delete()
    return signing.dumps(
        {"nonce": nonce, "tenant": tenant_schema},
        salt=LOGIN_HANDOFF_SALT,
        compress=True,
    )


def _signup_client_ip(request):
    candidate = request.META.get("REMOTE_ADDR", "unknown")
    if settings.SIGNUP_TRUST_X_REAL_IP:
        candidate = request.META.get("HTTP_X_REAL_IP", candidate)
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return "unknown"


def _rate_limit_key(prefix, scope, identity):
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"{prefix}:{scope}:{digest}"


def _consume_rate_limit(prefix, scope, identity, limit, timeout):
    key = _rate_limit_key(prefix, scope, identity)
    if cache.add(key, 1, timeout=timeout):
        return True
    try:
        count = cache.incr(key)
    except ValueError:
        # La chiave puo' scadere tra add() e incr(): un solo retry atomico.
        if cache.add(key, 1, timeout=timeout):
            return True
        count = cache.incr(key)
    return count <= limit


def _signup_rate_limit_allows(request):
    email = request.POST.get("email", "").strip().lower() or "missing"
    client_ip = _signup_client_ip(request)
    timeout = settings.SIGNUP_RATE_LIMIT_WINDOW_SECONDS
    checks = (
        ("global", "all", settings.SIGNUP_RATE_LIMIT_GLOBAL),
        ("ip", client_ip, settings.SIGNUP_RATE_LIMIT_IP),
        ("email", email, settings.SIGNUP_RATE_LIMIT_EMAIL),
    )
    return all(
        _consume_rate_limit("signup-rate", scope, identity, limit, timeout)
        for scope, identity, limit in checks
    )


def _login_rate_limit_allows(request):
    email = request.POST.get("login", "").strip().lower() or "missing"
    client_ip = _signup_client_ip(request)
    timeout = settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS
    checks = (
        ("global", "all", settings.LOGIN_RATE_LIMIT_GLOBAL),
        ("ip", client_ip, settings.LOGIN_RATE_LIMIT_IP),
        ("email", email, settings.LOGIN_RATE_LIMIT_EMAIL),
    )
    return all(
        _consume_rate_limit("login-rate", scope, identity, limit, timeout)
        for scope, identity, limit in checks
    )


def _claim_signup_provisioning(slug, email, client_ip, **extra):
    values = {
        "email": email,
        "client_ip_hash": hashlib.sha256(client_ip.encode()).hexdigest(),
        "status": SignupProvisioning.STATUS_PENDING,
        "error_code": "",
        "cleanup_required": False,
        "completed_at": None,
        "verified_at": None,
        **extra,
    }
    try:
        with transaction.atomic():
            return SignupProvisioning.objects.create(slug=slug, **values)
    except IntegrityError:
        now = timezone.now()
        claimed = SignupProvisioning.objects.filter(
            slug=slug,
            status=SignupProvisioning.STATUS_FAILED,
        ).update(**values)
        if claimed != 1:
            claimed = SignupProvisioning.objects.filter(
                slug=slug,
                status=SignupProvisioning.STATUS_PENDING,
                expires_at__lt=now,
            ).update(**values)
        if claimed != 1:
            return None
        return SignupProvisioning.objects.get(slug=slug)


def _reserve_pending_signup(slug, email, client_ip, company_name, password_hash):
    """Una registrazione pending per email; niente tenant finche' non conferma."""
    extra = {
        "company_name": company_name,
        "password_hash": password_hash,
        "token_hash": None,
    }
    pending = SignupProvisioning.objects.filter(
        email__iexact=email,
        status=SignupProvisioning.STATUS_PENDING,
    ).first()
    if pending is None:
        return _claim_signup_provisioning(
            slug, email, client_ip, **extra,
        )

    now = timezone.now()
    if pending.slug != slug:
        holder = SignupProvisioning.objects.filter(slug=slug).exclude(pk=pending.pk).first()
        if holder is not None:
            expired_pending = (
                holder.status == SignupProvisioning.STATUS_PENDING
                and holder.expires_at is not None
                and holder.expires_at < now
            )
            if holder.status != SignupProvisioning.STATUS_FAILED and not expired_pending:
                return None
            if expired_pending or holder.status == SignupProvisioning.STATUS_FAILED:
                holder.slug = f"expired-{holder.pk}-{holder.slug}"[:63]
                holder.save(update_fields=["slug"])
        if Client.objects.filter(schema_name=slug).exists():
            return None
        pending.slug = slug
    pending.company_name = company_name
    pending.password_hash = password_hash
    pending.client_ip_hash = hashlib.sha256(client_ip.encode()).hexdigest()
    pending.error_code = ""
    pending.cleanup_required = False
    pending.completed_at = None
    pending.verified_at = None
    pending.save(update_fields=[
        "slug",
        "company_name",
        "password_hash",
        "client_ip_hash",
        "error_code",
        "cleanup_required",
        "completed_at",
        "verified_at",
        "updated_at",
    ])
    return pending


def _signup_confirm_url(token):
    base = getattr(settings, "SIGNUP_VERIFY_URL_BASE", "https://zeus.cais.uno")
    return f"{base.rstrip('/')}/accounts/signup/confirm/?t={token}"


def _issue_signup_verification(provisioning):
    nonce = secrets.token_urlsafe(32)
    now = timezone.now()
    ttl = settings.SIGNUP_VERIFY_TTL_SECONDS
    token_hash = hashlib.sha256(nonce.encode()).hexdigest()
    SignupProvisioning.objects.filter(pk=provisioning.pk).update(
        token_hash=token_hash,
        expires_at=now + timedelta(seconds=ttl),
        status=SignupProvisioning.STATUS_PENDING,
        verified_at=None,
    )
    SignupProvisioning.objects.filter(
        status=SignupProvisioning.STATUS_PENDING,
        expires_at__lt=now,
    ).exclude(pk=provisioning.pk).update(token_hash=None)
    return signing.dumps(
        {"nonce": nonce, "email": provisioning.email},
        salt=SIGNUP_VERIFY_SALT,
        compress=True,
    )


def _send_signup_verification_email(email, company_name, token):
    confirm_url = _signup_confirm_url(token)
    send_mail(
        subject="Conferma il tuo account ZEUS",
        message=(
            "Per attivare il workspace "
            f"{company_name} apri questo link (valido 24 ore):\n\n"
            f"{confirm_url}\n\n"
            "Se non hai chiesto tu questo account, ignora il messaggio.\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )


def _create_tenant_owner(email, password_hash):
    user = get_user_model()(
        username=email[:150],
        email=email,
        is_active=True,
    )
    user.password = password_hash
    user.save()
    try:
        from allauth.account.models import EmailAddress
    except Exception:
        return user
    EmailAddress.objects.update_or_create(
        user=user,
        email=email,
        defaults={"primary": True, "verified": True},
    )
    return user


def _cleanup_failed_signup(tenant, email, tenant_domain):
    cleanup_ok = True
    try:
        WorkspaceAccess.objects.filter(
            email__iexact=email,
            tenant_domain=tenant_domain,
        ).delete()
    except Exception:
        cleanup_ok = False
        logger.exception("signup_workspace_access_cleanup_failed")

    if tenant is not None and tenant.pk and Client.objects.filter(pk=tenant.pk).exists():
        try:
            tenant.delete(force_drop=True)
        except Exception:
            cleanup_ok = False
            logger.exception("signup_tenant_cleanup_failed", extra={"tenant": tenant.schema_name})
    return cleanup_ok


def _provision_workspace(email, slug, company_name, create_user, provisioning):
    tenant_domain = f"{slug}.zeus.cais.uno"
    tenant = None
    try:
        tenant = Client(schema_name=slug, name=company_name)
        tenant.save()

        domain = Domain.objects.create(
            domain=tenant_domain,
            tenant=tenant,
            is_primary=True,
        )
        WorkspaceAccess.objects.create(email=email, tenant_domain=domain.domain)
        WorkspaceSubscription.objects.create(client=tenant, plan=Plan.get_default())

        with schema_context(slug):
            user = create_user()

        handoff_token = _create_login_handoff(slug, user)
    except Exception as exc:
        cleanup_ok = _cleanup_failed_signup(tenant, email, tenant_domain)
        SignupProvisioning.objects.filter(pk=provisioning.pk).update(
            status=SignupProvisioning.STATUS_FAILED,
            error_code=exc.__class__.__name__[:100],
            cleanup_required=not cleanup_ok,
        )
        raise

    SignupProvisioning.objects.filter(pk=provisioning.pk).update(
        status=SignupProvisioning.STATUS_COMPLETED,
        completed_at=timezone.now(),
    )
    return domain, handoff_token


def _provision_signup(request, form, provisioning):
    return _provision_workspace(
        email=form.cleaned_data["email"],
        slug=form.cleaned_data["company_slug"],
        company_name=form.cleaned_data["company_name"],
        create_user=lambda: form.save(request),
        provisioning=provisioning,
    )


def _signup_confirm_error(request):
    return render(
        request,
        "account/signup_confirm_error.html",
        status=400,
    )


@require_GET
def signup_confirm(request):
    """Attiva il workspace solo dopo il click sul link monouso inviato via email."""
    raw_token = request.GET.get("t", "")
    if not raw_token or len(raw_token) > 1024:
        return _signup_confirm_error(request)

    try:
        payload = signing.loads(
            raw_token,
            salt=SIGNUP_VERIFY_SALT,
            max_age=settings.SIGNUP_VERIFY_TTL_SECONDS,
        )
    except (BadSignature, SignatureExpired):
        return _signup_confirm_error(request)

    nonce = payload.get("nonce") if isinstance(payload, dict) else None
    email = payload.get("email") if isinstance(payload, dict) else None
    if not isinstance(nonce, str) or not isinstance(email, str):
        return _signup_confirm_error(request)

    token_hash = hashlib.sha256(nonce.encode()).hexdigest()
    now = timezone.now()
    provisioning = SignupProvisioning.objects.filter(
        token_hash=token_hash,
        email__iexact=email,
        status=SignupProvisioning.STATUS_PENDING,
        expires_at__gt=now,
        verified_at__isnull=True,
    ).first()
    if provisioning is None or not provisioning.password_hash:
        return _signup_confirm_error(request)

    consumed = SignupProvisioning.objects.filter(
        pk=provisioning.pk,
        token_hash=token_hash,
        status=SignupProvisioning.STATUS_PENDING,
        verified_at__isnull=True,
    ).update(verified_at=now, token_hash=None)
    if consumed != 1:
        return _signup_confirm_error(request)
    provisioning.refresh_from_db()

    try:
        domain, handoff_token = _provision_workspace(
            email=provisioning.email,
            slug=provisioning.slug,
            company_name=provisioning.company_name or provisioning.slug,
            create_user=lambda: _create_tenant_owner(
                provisioning.email,
                provisioning.password_hash,
            ),
            provisioning=provisioning,
        )
    except Exception:
        logger.exception(
            "signup_confirm_provisioning_failed",
            extra={"tenant": provisioning.slug},
        )
        return render(
            request,
            "account/signup_confirm_error.html",
            {"provision_failed": True},
            status=503,
        )

    response = redirect(f"https://{domain.domain}/accounts/handoff/?t={handoff_token}")
    return _set_workspace_cookie(response, domain.domain)


class ZEUSSignupView(SignupView):
    form_class = ZEUSSignupForm
    template_name = "account/signup.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            auth_logout(request)
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        try:
            allowed = _signup_rate_limit_allows(request)
        except Exception:
            logger.exception("signup_rate_limit_unavailable")
            return render(
                request,
                self.template_name,
                {"form": self.form_class(), "signup_unavailable": True},
                status=503,
            )
        if not allowed:
            response = render(
                request,
                self.template_name,
                {"form": self.form_class(), "signup_rate_limited": True},
                status=429,
            )
            response["Retry-After"] = str(settings.SIGNUP_RATE_LIMIT_WINDOW_SECONDS)
            return response
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        slug = form.cleaned_data["company_slug"]
        email = form.cleaned_data["email"]
        company_name = form.cleaned_data["company_name"]
        if WorkspaceAccess.objects.filter(email__iexact=email).exists():
            form.add_error("email", "This email is already registered in another workspace.")
            return self.form_invalid(form)

        provisioning = _reserve_pending_signup(
            slug,
            email,
            _signup_client_ip(self.request),
            company_name,
            make_password(form.cleaned_data["password1"]),
        )
        if provisioning is None:
            form.add_error(None, "Provisioning gia' in corso o workspace gia' creato.")
            response = self.form_invalid(form)
            response.status_code = 409
            return response

        try:
            token = _issue_signup_verification(provisioning)
            _send_signup_verification_email(email, company_name, token)
        except Exception:
            logger.exception("signup_verification_email_failed", extra={"tenant": slug})
            return render(
                self.request,
                self.template_name,
                {"form": self.form_class(), "signup_unavailable": True},
                status=503,
            )

        return render(
            self.request,
            "account/signup_check_email.html",
            {"email": email},
        )


def public_login(request):
    """Login su zeus.cais.uno: autentica qui, poi handoff monouso al tenant.

    Con le sessioni host-only (Codex Security finding 1) la sessione creata
    sul public host NON varrebbe sul tenant: si emette quindi un token
    usa-e-getta che la view login_handoff consuma sull'host del tenant,
    dove avviene il vero auth_login (cookie host-only)."""
    if request.method == "POST":
        try:
            allowed = _login_rate_limit_allows(request)
        except Exception:
            logger.exception("login_rate_limit_unavailable")
            return render(
                request,
                "account/login.html",
                {"error": "Accesso temporaneamente non disponibile. Riprova più tardi."},
                status=503,
            )
        if not allowed:
            response = render(
                request,
                "account/login.html",
                {"error": "Troppi tentativi di accesso. Riprova tra qualche minuto."},
                status=429,
            )
            response["Retry-After"] = str(settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS)
            return response

        email = request.POST.get("login", "").strip()
        password = request.POST.get("password", "")
        access = WorkspaceAccess.objects.filter(email__iexact=email).first()
        if not access:
            return render(request, "account/login.html", {
                "error": "Email o password non validi.",
            })

        tenant_schema = access.tenant_domain.split(".zeus.cais.uno", 1)[0]
        with schema_context(tenant_schema):
            user = authenticate(request, username=email, password=password)

        if user is None:
            return render(request, "account/login.html", {
                "error": "Email o password non validi.",
            })

        handoff_token = _create_login_handoff(tenant_schema, user)
        response = redirect(f"https://{access.tenant_domain}/accounts/handoff/?t={handoff_token}")
        return _set_workspace_cookie(response, access.tenant_domain)

    return render(request, "account/login.html", {"error": None})


def _login_handoff_error(request):
    response = render(request, "account/login.html", {
        "error": "Link di accesso scaduto o gia' usato. Ripeti il login.",
    })
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


@require_GET
def login_handoff(request):
    """Consuma il token monouso e apre la sessione host-only sul tenant.

    Il consumo e' atomico (UPDATE ... WHERE consumed_at IS NULL): un token
    gia' usato o scaduto non apre alcuna sessione. Il token vale solo per
    il tenant per cui e' stato emesso."""
    raw_token = request.GET.get("t", "")
    tenant = getattr(request, "tenant", None)
    tenant_schema = getattr(tenant, "schema_name", "public")
    if not raw_token or len(raw_token) > 1024 or tenant_schema == "public":
        return _login_handoff_error(request)

    try:
        payload = signing.loads(
            raw_token,
            salt=LOGIN_HANDOFF_SALT,
            max_age=LOGIN_HANDOFF_TTL_SECONDS,
        )
    except (BadSignature, SignatureExpired):
        return _login_handoff_error(request)

    nonce = payload.get("nonce") if isinstance(payload, dict) else None
    signed_tenant = payload.get("tenant") if isinstance(payload, dict) else None
    if not isinstance(nonce, str) or signed_tenant != tenant_schema:
        return _login_handoff_error(request)

    token_hash = hashlib.sha256(nonce.encode()).hexdigest()
    now = timezone.now()
    handoff = LoginHandoff.objects.filter(token_hash=token_hash).first()
    # Il check tenant NON consuma il token: presentato sul tenant sbagliato
    # resta valido per il legittimo proprietario.
    if handoff is None or handoff.tenant_schema != tenant_schema:
        return _login_handoff_error(request)

    from django.contrib.auth import get_user_model

    with schema_context(tenant_schema):
        user = get_user_model().objects.filter(pk=handoff.user_id, is_active=True).first()
    if user is None:
        return _login_handoff_error(request)

    consumed = LoginHandoff.objects.filter(
        token_hash=token_hash,
        consumed_at__isnull=True,
        expires_at__gt=now,
    ).update(consumed_at=now)
    if consumed != 1:
        return _login_handoff_error(request)

    auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    response = redirect(settings.LOGIN_REDIRECT_URL)
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


def tenant_landing(request):
    tenant = request.tenant if hasattr(request, "tenant") else None
    is_public = tenant is None or tenant.schema_name == "public"
    if is_public and request.user.is_authenticated:
        workspace = _valid_workspace_cookie(request)
        if workspace:
            return redirect(f"https://{workspace}/onboarding/")
    return render(request, "core/tenant_landing.html", {
        "tenant": tenant,
        "is_public": is_public,
    })


def public_onboarding_redirect(request):
    """Redirect intelligente da zeus.cais.uno/onboarding/ al workspace corretto."""
    return redirect_to_workspace_or_login(request)


@login_required
def tenant_dashboard(request):
    tenant = request.tenant if hasattr(request, "tenant") else None
    template_name = (
        "core/app_shell_dashboard.html"
        if settings.ZEUS_APP_SHELL_ENABLED
        else "core/tenant_dashboard.html"
    )
    return render(request, template_name, {
        "tenant": tenant,
        "user": request.user,
    })


def _revoke_session_key(session_key):
    """Delete a session from the configured store. Missing keys are a no-op."""
    if not session_key:
        return
    engine = import_module(settings.SESSION_ENGINE)
    engine.SessionStore(session_key).delete()


def public_logout(request):
    """End both app and admin sessions. GET only confirms; POST performs logout."""
    if request.method != "POST":
        return render(request, "account/logout.html")

    # Path /accounts/logout/ loads the app session. Flush it, then also
    # delete the independent admin session so a copied cookie cannot replay.
    admin_session_key = request.COOKIES.get(settings.ADMIN_SESSION_COOKIE_NAME)
    auth_logout(request)
    _revoke_session_key(admin_session_key)

    response = redirect("https://zeus.cais.uno/accounts/login/")
    response.delete_cookie(
        settings.SESSION_COOKIE_NAME, domain=settings.SESSION_COOKIE_DOMAIN
    )
    response.delete_cookie(settings.ADMIN_SESSION_COOKIE_NAME)
    return _clear_workspace_cookie(response)
