import time
import logging
import threading
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


class Command(BaseCommand):
    help = 'Run payment service RabbitMQ consumers'

    def handle(self, *args, **options):
        from api.messaging.consumer.user import AuthEventConsumer
        from api.messaging.consumer.infrastructure import InfraEventConsumer

        if not _wait_for_db():
            self.stderr.write('DB not ready, aborting')
            return

        def _start(consumer_cls, name):
            try:
                logger.info(f"Starting {name}...")
                consumer_cls().start()
            except Exception as e:
                logger.error(f"{name} crashed: {e}", exc_info=True)

        threads = [
            threading.Thread(target=_start, args=(AuthEventConsumer, 'AuthEventConsumer'), daemon=True),
            threading.Thread(target=_start, args=(InfraEventConsumer, 'InfraEventConsumer'), daemon=True),
        ]
        for t in threads:
            t.start()

        logger.info("Payment service consumers running")
        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            logger.info("Shutting down")
