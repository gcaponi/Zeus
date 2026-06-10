from .base import *

DEBUG = False

ALLOWED_HOSTS = [".zeus.cais.uno", "91.230.110.7", "localhost", "127.0.0.1"]

CSRF_TRUSTED_ORIGINS = ["https://*.zeus.cais.uno", "https://zeus.cais.uno"]
CSRF_COOKIE_DOMAIN = ".zeus.cais.uno"
SESSION_COOKIE_DOMAIN = ".zeus.cais.uno"
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True

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

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}