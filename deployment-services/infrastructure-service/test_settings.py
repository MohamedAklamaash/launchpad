"""Minimal Django settings for running pytest against the infrastructure-service.

Uses an in-memory SQLite DB so model tables can be created without a live Postgres
instance, and skips production middleware that would require Redis/RabbitMQ.
"""
import os

# Stub the env vars validate_config() requires. These are NOT real secrets.
os.environ.setdefault("DJANGO_SECRET", "x" * 60)
os.environ.setdefault("JWT_SECRET", "x" * 40)
os.environ.setdefault("DJANGO_PORT", "8002")
os.environ.setdefault("INTERNAL_API_TOKEN", "x" * 40)
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("DATABASE_USER_NAME", "test")
os.environ.setdefault("DATABASE_PASSWORD", "test")
os.environ.setdefault("DATABASE_HOST", "localhost")
os.environ.setdefault("DATABASE_PORT", "5432")
os.environ.setdefault("DATABASE_NAME", "test")
os.environ.setdefault("INFRASTRUCTURE_DB_URL", "postgres://test:test@localhost/test")

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

EKS_ENABLED = os.environ.get("EKS_ENABLED", "False").lower() == "true"
EKS_PUBLIC_ACCESS_CIDRS = [
    c.strip() for c in os.environ.get("EKS_PUBLIC_ACCESS_CIDRS", "").split(",") if c.strip()
]

LOGGING_CONFIG = None

DATABASE_ENGINE_VERSIONS = {
    "postgres": {"15.10", "16.6", "17.2"},
    "mysql": {"8.0.39"},
    "redis": {"7.1"},
    "docdb": {"5.0.0"},
}
DATABASE_INSTANCE_CLASSES = {
    "postgres": {"db.t3.micro", "db.t3.small", "db.t3.medium", "db.r6g.large"},
    "mysql": {"db.t3.micro", "db.t3.small", "db.t3.medium", "db.r6g.large"},
    "redis": {"cache.t3.micro", "cache.t3.small", "cache.t3.medium", "cache.r6g.large"},
    "docdb": {"db.t3.medium", "db.r6g.large"},
}
DATABASE_MIN_STORAGE_GB = 20
DATABASE_MAX_STORAGE_GB = 1000
MAX_DATABASES_PER_INFRA = 10
