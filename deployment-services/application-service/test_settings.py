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
# Deliberately unroutable (port 1 is never a broker) — defense in depth alongside the
# root conftest.py `no_real_broker` fixture, which patches ResilientPikaProducer's
# connect/publish for every test. If that patch is ever bypassed, a real connection
# attempt fails fast here instead of landing on whatever broker happens to be running on
# the developer's machine at the default port (see shared/resilience/amqp.py's connect()
# guard for the other half of this backstop). Assigned outright, not setdefault: a
# developer with RABBITMQ_URL exported in their shell (pointing at their real local
# broker, exactly the failure mode this guards against) must not be able to leak it in.
os.environ["RABBITMQ_URL"] = "amqp://guest:guest@127.0.0.1:1/"
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

RATE_BUDGET_RUNTIME_LOGS_LIMIT = 30
RATE_BUDGET_RUNTIME_LOGS_WINDOW_SECONDS = 60
RUNTIME_LOGS_CURSOR_SECRET = "x" * 40

PUBLIC_GATEWAY_URL = "http://localhost:8000"

LOGGING_CONFIG = None

# Per-app ceiling for Kubernetes applications. Fargate has a fixed CPU/memory ladder
# topping out at 4 vCPU / 30 GB; Kubernetes has no such ladder, so it gets its own
# ceiling rather than silently inheriting Fargate's. The infrastructure's
# max_cpu/max_memory quota still applies on top of this.
EKS_MAX_APP_CPU = float(os.environ.get('EKS_MAX_APP_CPU', '64'))
EKS_MAX_APP_MEMORY = float(os.environ.get('EKS_MAX_APP_MEMORY', '256'))

MAX_SNI_CERTIFICATES_PER_LISTENER = 24
INFRASTRUCTURE_SERVICE_URL = "http://localhost:8002"

# H1: test-only key for Application.envs encryption (api/fields.py), generated fresh each
# test run rather than a fixed literal — a committed key-shaped string gets flagged by
# secret scanners (and is exactly the "copy this into prod" trap load_keys guards
# against) even when it's genuinely only ever used by pytest. See core/settings.py for the
# real MODE=dev/prod key-loading rules, which this file bypasses entirely (test_settings is
# not core.settings).
from cryptography.fernet import Fernet as _Fernet

APP_ENVS_ENCRYPTION_KEYS = (_Fernet.generate_key().decode(),)
