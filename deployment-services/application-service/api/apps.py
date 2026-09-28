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


def _envs_decryption_canary() -> None:
    """H1 security review (REC2): a wrong or missing APP_ENVS_ENCRYPTION_KEYS must fail
    the process at startup, not surface as a 500 on the first request that touches an
    Application row. Reads one row's raw envs column and tries to decrypt it.

    Silently returns (does not crash) when there is nothing yet to verify: the table
    doesn't exist (fresh install, or a `migrate` run that hasn't reached 0036 yet — that
    run's own `ready()` call happens before its migrations apply), the table is reachable
    but empty, or the value is plain JSON text rather than ciphertext
    (`EnvsNotMigratedError` — a schema/data-state question, not a key question; verified
    against real Postgres that a still-`jsonb` column reads through Django's connection as
    JSON text, not a `dict`, so this is the branch that actually catches it in practice).
    Anything else that fails to decrypt is exactly what this check exists to catch.
    """
    from django.db import connection
    from django.db.utils import DatabaseError

    from api.fields import EnvsDecryptionError, EnvsNotMigratedError, decrypt_value
    from api.models.application import Application

    table = Application._meta.db_table
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT envs FROM {table} WHERE envs IS NOT NULL LIMIT 1")
            row = cursor.fetchone()
    except DatabaseError:
        return
    finally:
        connection.close()

    if row is None:
        return

    try:
        decrypt_value(row[0])
    except EnvsNotMigratedError:
        return
    except EnvsDecryptionError:
        logger.critical(
            "APP_ENVS_ENCRYPTION_KEYS cannot decrypt an existing Application.envs row — "
            "refusing to start. Check the configured key(s) match what encrypted existing "
            "data (see rotate_envs_encryption_key / repair_envs_encryption)."
        )
        raise


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"

    def ready(self):
        """Start RabbitMQ consumers when the server starts."""
        from shared.mode import enforce_dev_mode_safety

        from api.common.envs.application import app_config
        enforce_dev_mode_safety(app_config.mode, "application-service", logger)
        _envs_decryption_canary()

        if os.environ.get("RUN_MAIN") != "true" and "runserver" in sys.argv:
            return

        from api.messaging.consumers.environment import EnvironmentEventConsumer
        from api.messaging.consumers.infrastructure import (
            HostReadinessEventConsumer,
            InfraDeletedEventConsumer,
            InfraEventConsumer,
            InfraExitedEventConsumer,
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

        def start_host_readiness_consumer():
            try:
                if not _wait_for_db():
                    return
                logger.info("Initializing Application Service HostReadinessEventConsumer…")
                HostReadinessEventConsumer().start()
            except Exception:
                logger.exception("HostReadinessEventConsumer crashed")

        def start_infra_exited_consumer():
            try:
                if not _wait_for_db():
                    return
                logger.info("Initializing Application Service InfraExitedEventConsumer…")
                InfraExitedEventConsumer().start()
            except Exception:
                logger.exception("InfraExitedEventConsumer crashed")

        threading.Thread(target=start_infra_consumer, name="AppInfraConsumer", daemon=True).start()
        threading.Thread(target=start_infra_updated_consumer, name="AppInfraUpdatedConsumer", daemon=True).start()
        threading.Thread(target=start_infra_deleted_consumer, name="AppInfraDeletedConsumer", daemon=True).start()
        threading.Thread(target=start_infra_user_removed_consumer, name="AppInfraUserRemovedConsumer", daemon=True).start()
        threading.Thread(target=start_auth_consumer, name="AppAuthConsumer", daemon=True).start()
        threading.Thread(target=start_environment_consumer, name="AppEnvConsumer", daemon=True).start()
        threading.Thread(target=start_host_readiness_consumer, name="AppHostReadinessConsumer", daemon=True).start()
        threading.Thread(target=start_infra_exited_consumer, name="AppInfraExitedConsumer", daemon=True).start()
        logger.info("Application Service messaging threads scheduled.")
