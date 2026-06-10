from .base import *

DEBUG = False

ALLOWED_HOSTS = [".zeus.cais.uno", "91.230.110.7", "localhost", "127.0.0.1"]

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
        "BACKEND": "django_tenants.postgresql_backend",
    }
}