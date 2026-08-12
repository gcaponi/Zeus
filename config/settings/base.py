import os
from pathlib import Path

from apps.core.observability import configure_sentry, configure_structured_logging

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "change-me-in-production")
DEBUG = False
ALLOWED_HOSTS = []
ZEUS_APP_SHELL_ENABLED = os.environ.get("ZEUS_APP_SHELL_ENABLED", "").lower() in {
    "1",
    "true",
    "yes",
}

# Nomi distinti e ruotati per le sessioni app e admin. Il rename dell'app
# rende innocuo il vecchio cookie `sessionid` con Domain=.zeus.cais.uno che
# puo' restare nei browser dopo il passaggio ai cookie host-only.
SESSION_COOKIE_NAME = "zeus_app_sessionid"

# Cookie di sessione dedicato alla zona admin (/admin/, /zeus-admin/):
# separa le sessioni admin (public schema) da quelle app (tenant schema),
# che altrimenti si invaliderebbero a vicenda (rotazione chiave + utente di
# schema diverso) con logout a cascata. Usato da TenantAwareSessionMiddleware.
# Cookie di sessione dedicato alla zona admin (vedi TenantAwareSessionMiddleware).
# Rinominato (era "admin_sessionid") in occasione del passaggio a cookie
# host-only: il vecchio cookie con Domain=.zeus.cais.uno resta orfano nei
# browser e non viene piu' letto, niente collisioni nome/dominio.
ADMIN_SESSION_COOKIE_NAME = "zeus_admin_sessionid"

SHARED_APPS = [
    "django_tenants",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.admin",
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "apps.core",
    "apps.zeus_admin",
]

TENANT_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "allauth",
    "allauth.account",
    "apps.companies",
]

INSTALLED_APPS = list(SHARED_APPS) + [app for app in TENANT_APPS if app not in SHARED_APPS]

SITE_ID = 1

MIDDLEWARE = [
    "django_tenants.middleware.main.TenantMainMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.TenantAwareSessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.CompanyAnagraficaRequiredMiddleware",
    "apps.core.middleware.RequestContextLoggingMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django_tenants.postgresql_backend",
        "NAME": os.environ.get("POSTGRES_DB", "mydb"),
        "USER": os.environ.get("POSTGRES_USER", "myuser"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "mypassword"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

DATABASE_ROUTERS = ["django_tenants.routers.TenantSyncRouter"]

TENANT_MODEL = "core.Client"
TENANT_DOMAIN_MODEL = "core.Domain"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/"

LANGUAGE_CODE = "it-it"
TIME_ZONE = "Europe/Rome"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "zeus-local",
    },
}

METRICS_TOKEN = os.environ.get("METRICS_TOKEN", "")
METRICS_CACHE_SECONDS = int(os.environ.get("METRICS_CACHE_SECONDS", "60"))
SIGNUP_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("SIGNUP_RATE_LIMIT_WINDOW_SECONDS", "3600"))
SIGNUP_RATE_LIMIT_GLOBAL = int(os.environ.get("SIGNUP_RATE_LIMIT_GLOBAL", "20"))
SIGNUP_RATE_LIMIT_IP = int(os.environ.get("SIGNUP_RATE_LIMIT_IP", "5"))
SIGNUP_RATE_LIMIT_EMAIL = int(os.environ.get("SIGNUP_RATE_LIMIT_EMAIL", "3"))
SIGNUP_TRUST_X_REAL_IP = False

# Unita' astratte di lavoro pagato (una chat = 1; una generazione multi-call
# riserva piu' unita'). Limiti volutamente larghi per uso legittimo, ma finiti
# per impedire spesa e fan-out illimitati da un singolo tenant.
PAID_OPERATION_DAILY_UNIT_LIMITS = {
    "starter": 500,
    "professional": 2500,
    "enterprise": 10000,
}
PAID_OPERATION_CONCURRENCY_LIMITS = {
    "starter": 2,
    "professional": 4,
    "enterprise": 8,
}
PAID_OPERATION_STALE_SECONDS = int(os.environ.get("PAID_OPERATION_STALE_SECONDS", "1800"))

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

LOGGING = configure_structured_logging()
configure_sentry(DEBUG)
