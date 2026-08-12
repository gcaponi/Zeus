import os

from .base import *  # noqa: F403

DEBUG = False

ALLOWED_HOSTS = [".zeus.cais.uno", "91.230.110.7", "localhost", "127.0.0.1"]

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Niente wildcard https://*.zeus.cais.uno e niente cookie CSRF sul dominio
# condiviso: un tenant sibling potrebbe altrimenti leggere il token CSRF e
# forgiare richieste cross-tenant/admin (Codex Security finding 3). I form
# e le chiamate HTMX sono same-origin: non serve alcuna trusted origin extra.
CSRF_TRUSTED_ORIGINS = ["https://zeus.cais.uno"]
# Nessun SESSION_COOKIE_DOMAIN: cookie di sessione host-only su ogni host
# (public, tenant, admin). Il login attraversa gli host via LoginHandoff
# monouso (Codex Security finding 1 — chiusura completa).
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_AGE = 28800
CSRF_COOKIE_SECURE = True

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

DATABASES = {
    "default": {
        "ENGINE": "django_tenants.postgresql_backend",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ["POSTGRES_HOST"],
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,  # noqa: F405
        "KEY_PREFIX": "zeus",
    },
}

# Nginx sovrascrive X-Real-IP con l'indirizzo della connessione client.
SIGNUP_TRUST_X_REAL_IP = True

STORAGES = {
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
