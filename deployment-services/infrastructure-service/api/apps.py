import os
import sys
import time
import threading
import logging
import getpass
from crontab import CronTab
from django.apps import AppConfig

logger = logging.getLogger(__name__)


def _wait_for_db(max_wait: int = 60, interval: int = 3) -> bool:
    """
    Block until the DB is accessible and migrations have been applied.
    Returns True when ready, False if timed out.
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

def setup_cron_job():
    """Sets up the automated cron job when the app starts."""
    try:
        current_user = getpass.getuser()
        cron = CronTab(user=current_user)
        

        
        cron.write()
        logger.info("Successfully configured automated cron job for Compute Optimizer.")
    except Exception as e:
        logger.error(f"Failed to setup cron job: {e}")



class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"

    def ready(self):
        """Start RabbitMQ consumers when the server starts."""
        if os.environ.get("RUN_MAIN") != "true" and "runserver" in sys.argv:
            return

        from api.messaging.consumer.consumer import AuthEventConsumer
        from api.messaging.consumer.application_consumer import start_application_event_consumer
        from api.common.envs.application import app_config

        def start_auth_consumer():
            # Small delay to ensure the main thread has completed app initialization,
            time.sleep(2)
            try:
                if not _wait_for_db():
                    return
                logger.info("Initializing Infrastructure Service AuthEventConsumer…")
                AuthEventConsumer().start()
            except Exception as exc:
                logger.error(f"Infrastructure Service AuthEventConsumer crashed: {exc}", exc_info=True)

        def start_app_consumer():
            time.sleep(2)
            try:
                if not _wait_for_db():
                    return
                logger.info("Initializing Infrastructure Service ApplicationEventConsumer…")
                start_application_event_consumer(app_config.rabbitmq_url)
            except Exception as exc:
                logger.error(f"Infrastructure Service ApplicationEventConsumer crashed: {exc}", exc_info=True)

        threading.Thread(target=start_auth_consumer, name="InfraAuthConsumer", daemon=True).start()
        threading.Thread(target=start_app_consumer, name="InfraAppConsumer", daemon=True).start()
        logger.info("Infrastructure Service messaging thread scheduled.")

        # Automatically setup the cron job for Rightsizing Enforcement
        setup_cron_job()
