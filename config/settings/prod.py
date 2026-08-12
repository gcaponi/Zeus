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
SESSION_COOKIE_DOMAIN = ".zeus.cais.uno"
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

STORAGES = {
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
