import json
import logging
from django.core.management.base import BaseCommand
from api.services.deployment_queue import DeploymentQueue

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Inspect and optionally replay jobs from the deployment DLQ'

    def add_arguments(self, parser):
        parser.add_argument('--replay', action='store_true', help='Move all DLQ jobs back to the main queue')

    def handle(self, *args, **options):
        r = DeploymentQueue.get_redis()
        dlq = DeploymentQueue.DLQ_NAME
        depth = r.llen(dlq)
        self.stdout.write(f"DLQ depth: {depth}")

        if depth == 0:
            return

        jobs = r.lrange(dlq, 0, -1)
        for i, raw in enumerate(jobs):
            try:
                job = json.loads(raw)
                self.stdout.write(f"  [{i}] app_id={job.get('app_id')} action={job.get('action')} retries={job.get('retry_count')}")
            except Exception:
                self.stdout.write(f"  [{i}] <unparseable: {raw[:80]}>")

        if options['replay']:
            replayed = 0
            while True:
                raw = r.rpoplpush(dlq, DeploymentQueue.QUEUE_NAME)
                if not raw:
                    break
                # Reset retry count so replayed jobs get a fresh attempt
                try:
                    job = json.loads(raw)
                    job['retry_count'] = 0
                    r.lset(DeploymentQueue.QUEUE_NAME, -1, json.dumps(job))
                except Exception:
                    pass
                replayed += 1
            self.stdout.write(f"Replayed {replayed} job(s) from DLQ to main queue")
