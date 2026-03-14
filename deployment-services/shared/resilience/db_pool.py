import os
from typing import Dict, Any


def get_db_pool_config(db_config: Any, conn_max_age: int = None) -> Dict[str, Any]:
    """
    Returns a Django DATABASES config with connection pooling tuned for the process type.

    Pool sizing rationale:
      - Web (gunicorn): CONN_MAX_AGE > 0 keeps one connection per worker thread alive.
        With e.g. 4 workers × 2 threads = 8 persistent connections per service.
      - Worker processes: CONN_MAX_AGE=0 — connections are opened and closed per job,
        so they're never held idle between long-running tasks.

    Postgres max_connections=500 (set in docker-compose) gives plenty of headroom:
      ~3 Django services × 4 workers × 2 threads = ~24 web connections
      + worker processes (short-lived) = well under 500.
    """
    if conn_max_age is None:
        conn_max_age = int(os.environ.get("DB_CONN_MAX_AGE", 60))

    ssl = getattr(db_config, "ssl", False)

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": db_config.name,
        "USER": db_config.user_name,
        "PASSWORD": db_config.password,
        "HOST": db_config.host,
        "PORT": db_config.port,
        # CONN_MAX_AGE: seconds a connection is reused before being closed.
        # 0  = close after every request (workers, scripts)
        # 60 = reuse for 60s (web processes) — avoids per-request TCP overhead
        "CONN_MAX_AGE": conn_max_age,
        # Re-validate the connection before reuse (detects stale connections)
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "sslmode": "require" if ssl else "disable",
            "connect_timeout": 10,
            "options": (
                "-c statement_timeout=60000 "
                "-c idle_in_transaction_session_timeout=30000"
            ),
            # TCP keepalives — detect dead connections quickly
            "keepalives": 1,
            "keepalives_idle": 60,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
    }
