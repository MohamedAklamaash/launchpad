import logging
import threading
import time

from django.core.management.base import BaseCommand
from django.db import connection

logger = logging.getLogger(__name__)


def _wait_for_db(max_wait=60, interval=3):
    from django.db.utils import OperationalError, ProgrammingError
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        try:
            connection.ensure_connection()
            with connection.cursor() as c:
                c.execute("SELECT 1 FROM api_user LIMIT 1")
            return True
        except (OperationalError, ProgrammingError):
            time.sleep(interval)
        finally:
            connection.close()
    return False


def _start(consumer_cls, name):
    try:
        logger.info(f"Starting {name}...")
        consumer_cls().start()
    except Exception:
        logger.exception(f"{name} crashed")


def _org_consumer_threads():
    """F9 ticket 4: gated on ORGS_ENABLED (default off) — with the flag off, this returns
    no threads and nothing here opens a socket to the broker. Split out from handle() so
    the gate itself is directly testable without starting a real consumer thread."""
    from django.conf import settings

    if not settings.ORGS_ENABLED:
        logger.info("ORGS_ENABLED is False — org.events consumer not started")
        return []

    from api.messaging.consumer.org_events import OrgEventConsumer
    return [threading.Thread(target=_start, args=(OrgEventConsumer, 'OrgEventConsumer'), daemon=True)]


class Command(BaseCommand):
    help = 'Run payment service RabbitMQ consumers'

    def handle(self, *args, **options):
        from api.messaging.consumer.infrastructure import InfraEventConsumer
        from api.messaging.consumer.user import AuthEventConsumer

        if not _wait_for_db():
            self.stderr.write('DB not ready, aborting')
            return

        threads = [
            threading.Thread(target=_start, args=(AuthEventConsumer, 'AuthEventConsumer'), daemon=True),
            threading.Thread(target=_start, args=(InfraEventConsumer, 'InfraEventConsumer'), daemon=True),
            *_org_consumer_threads(),
        ]
        for t in threads:
            t.start()

        logger.info("Payment service consumers running")
        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            logger.info("Shutting down")
