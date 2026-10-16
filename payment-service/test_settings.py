"""Minimal Django settings for running pytest against payment-service.

Mirrors deployment-services/infrastructure-service/test_settings.py: in-memory SQLite
so model tables can be created without a live Postgres instance, and a minimal
INSTALLED_APPS/MIDDLEWARE that skips everything requiring Stripe/Redis/RabbitMQ.
payment-service had no pytest/Django test suite before this (see
api/common/utils/test_jwt.py's own note) -- this is the first one that needs the ORM.
"""
import os

# Stub the env vars ApplicationConfig.from_env()/DatabaseConfig.from_env() would
# require if anything on the import path reaches them. These are NOT real secrets.
os.environ.setdefault("DJANGO_SECRET", "x" * 60)
os.environ.setdefault("JWT_SECRET", "x" * 40)
os.environ.setdefault("DJANGO_PORT", "8004")
os.environ.setdefault("INTERNAL_API_TOKEN", "x" * 40)
os.environ["RABBITMQ_URL"] = "amqp://guest:guest@127.0.0.1:1/"
os.environ.setdefault("STRIPE_PUBLISHABLE_KEY", "x" * 20)
os.environ.setdefault("STRIPE_SECRET_KEY", "x" * 20)
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "x" * 20)
os.environ.setdefault("PUBLIC_BACKEND_URL", "http://localhost:8004")
os.environ.setdefault("DATABASE_USER_NAME", "test")
os.environ.setdefault("DATABASE_PASSWORD", "test")
os.environ.setdefault("DATABASE_HOST", "localhost")
os.environ.setdefault("DATABASE_PORT", "5432")
os.environ.setdefault("DATABASE_NAME", "test")
os.environ.setdefault("PAYMENTS_DB_URL", "postgres://test:test@localhost/test")

SECRET_KEY = "x" * 60
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "api",
    "rest_framework",
]

MIDDLEWARE = []
ROOT_URLCONF = "core.urls"
AUTH_USER_MODEL = "api.User"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (),
    "DEFAULT_PERMISSION_CLASSES": (),
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

INTERNAL_AUTH_EXEMPT_PATHS = []
INTERNAL_AUTH_HEADER_NAME = "X-INTERNAL-TOKEN"
INTERNAL_AUTH_TOKEN = "x" * 40

ORGS_ENABLED = os.environ.get("ORGS_ENABLED", "False").lower() == "true"
