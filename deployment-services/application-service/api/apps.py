import os
import sys
import time
import threading
import logging
from django.apps import AppConfig

logger = logging.getLogger(__name__)


def _wait_for_db(max_wait: int = 60, interval: int = 3) -> bool:
    """
        Consumer threads must not start until tables exists.
    """
    from django.db import connections
    from django.db.utils import OperationalError, ProgrammingError

    deadline = time.monotonic() + max_wait
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            conn = connections["default"]
            conn.ensure_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 FROM api_user LIMIT 1")
            logger.info(f"DB ready after {attempt} attempt(s)")
            return True
        except (OperationalError, ProgrammingError) as exc:
            logger.info(
                f"DB not ready yet (attempt {attempt}): {exc}. "
                f"Retrying in {interval}s…"
            )
            time.sleep(interval)
        except Exception as exc:
            logger.warning(f"Unexpected error waiting for DB: {exc}")
            time.sleep(interval)

    logger.error(
        f"DB did not become ready within {max_wait}s — consumers will NOT start. "
        "Run migrations and restart the service."
    )
    return False


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"

    def ready(self):
        """Start RabbitMQ consumers when the server starts."""
        if os.environ.get("RUN_MAIN") != "true" and "runserver" in sys.argv:
            return

        def start_infra_consumer():
            from api.messaging.consumers.infrastructure import InfraEventConsumer
            try:
                if not _wait_for_db():
                    return
                logger.info("Initializing Application Service InfraEventConsumer…")
                InfraEventConsumer().start()
            except Exception as exc:
                logger.error(f"InfraEventConsumer crashed: {exc}", exc_info=True)

        def start_auth_consumer():
            from api.messaging.consumers.user import AuthEventConsumer
            try:
                if not _wait_for_db():
                    return
                logger.info("Initializing Application Service AuthEventConsumer…")
                AuthEventConsumer().start()
            except Exception as exc:
                logger.error(f"AuthEventConsumer crashed: {exc}", exc_info=True)

        threading.Thread(target=start_infra_consumer, name="AppInfraConsumer", daemon=True).start()
        threading.Thread(target=start_auth_consumer, name="AppAuthConsumer", daemon=True).start()
        logger.info("Application Service messaging threads scheduled.")
