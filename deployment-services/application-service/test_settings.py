"""Minimal Django settings for running pytest against the application-service.

Mirrors infrastructure-service/test_settings.py: in-memory SQLite, no Redis/RabbitMQ
middleware. NOTE: a full migrate currently fails on the PRE-EXISTING Postgres-only
migration 0005 (ALTER TABLE ... ADD COLUMN IF NOT EXISTS), unrelated to MODE=dev.
"""
import os

os.environ.setdefault("MODE", "prod")
os.environ.setdefault("DJANGO_SECRET", "x" * 60)
os.environ.setdefault("JWT_SECRET", "x" * 40)
os.environ.setdefault("DJANGO_PORT", "8003")
os.environ.setdefault("INTERNAL_API_TOKEN", "x" * 40)
os.environ.setdefault("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("REDIS_PASSWORD", "")
os.environ.setdefault("REDIS_DB", "0")
os.environ.setdefault("DEPLOYMENT_MAX_INFRA_WORKERS", "5")

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
APPEND_SLASH = True
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

REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_PASSWORD = ""
REDIS_DB = 0

LOGGING_CONFIG = None
