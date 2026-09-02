import logging
import os
import sys
import threading
import time

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
        finally:
            conn.close()

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
        from shared.mode import enforce_dev_mode_safety

        from api.common.envs.application import app_config
        enforce_dev_mode_safety(app_config.mode, "application-service", logger)

        if os.environ.get("RUN_MAIN") != "true" and "runserver" in sys.argv:
            return

        from api.messaging.consumers.environment import EnvironmentEventConsumer
        from api.messaging.consumers.infrastructure import (
            InfraDeletedEventConsumer,
            InfraEventConsumer,
            InfraUpdatedEventConsumer,
            InfraUserRemovedEventConsumer,
        )
        from api.messaging.consumers.user import AuthEventConsumer

        def start_infra_consumer():
            try:
                if not _wait_for_db():
                    return
                logger.info("Initializing Application Service InfraEventConsumer…")
                InfraEventConsumer().start()
            except Exception:
                logger.exception("InfraEventConsumer crashed")
        
        def start_infra_updated_consumer():
            try:
                if not _wait_for_db():
                    return
                logger.info("Initializing Application Service InfraUpdatedEventConsumer…")
                InfraUpdatedEventConsumer().start()
            except Exception:
                logger.exception("InfraUpdatedEventConsumer crashed")

        def start_infra_deleted_consumer():
            try:
                if not _wait_for_db():
                    return
                logger.info("Initializing Application Service InfraDeletedEventConsumer…")
                InfraDeletedEventConsumer().start()
            except Exception:
                logger.exception("InfraDeletedEventConsumer crashed")

        def start_infra_user_removed_consumer():
            try:
                if not _wait_for_db():
                    return
                logger.info("Initializing Application Service InfraUserRemovedEventConsumer…")
                InfraUserRemovedEventConsumer().start()
            except Exception:
                logger.exception("InfraUserRemovedEventConsumer crashed")

        def start_auth_consumer():
            try:
                if not _wait_for_db():
                    return
                logger.info("Initializing Application Service AuthEventConsumer…")
                AuthEventConsumer().start()
            except Exception:
                logger.exception("AuthEventConsumer crashed")
        
        def start_environment_consumer():
            try:
                if not _wait_for_db():
                    return
                logger.info("Initializing Application Service EnvironmentEventConsumer…")
                EnvironmentEventConsumer().start()
            except Exception:
                logger.exception("EnvironmentEventConsumer crashed")

        threading.Thread(target=start_infra_consumer, name="AppInfraConsumer", daemon=True).start()
        threading.Thread(target=start_infra_updated_consumer, name="AppInfraUpdatedConsumer", daemon=True).start()
        threading.Thread(target=start_infra_deleted_consumer, name="AppInfraDeletedConsumer", daemon=True).start()
        threading.Thread(target=start_infra_user_removed_consumer, name="AppInfraUserRemovedConsumer", daemon=True).start()
        threading.Thread(target=start_auth_consumer, name="AppAuthConsumer", daemon=True).start()
        threading.Thread(target=start_environment_consumer, name="AppEnvConsumer", daemon=True).start()
        logger.info("Application Service messaging threads scheduled.")
