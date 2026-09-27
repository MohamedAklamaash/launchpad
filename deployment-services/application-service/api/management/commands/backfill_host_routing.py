"""F1b part 3a: migrate already-deployed ECS apps into host-mode routing once their
infrastructure becomes TLS-ready, without waiting for each app's next code push.

Host mode is applied automatically on an app's next ordinary deploy
(ApplicationDeploymentService._resolve_host_routing re-checks readiness every time), so this
command exists only to close the gap for an app that was last deployed before its infra
finished onboarding TLS and hasn't pushed code since. EKS needs no equivalent — EKSDeployer
re-resolves host_mode on every deploy the same way, and there is no separate task-definition
registration step to skip ahead of a code push there.

Idempotent and safe to run repeatedly: `ApplicationDeploymentService.backfill_host_routing`
skips anything not eligible (wrong compute type, not ACTIVE, already in host mode, infra not
TLS-ready, no addressable image to redeploy) and never touches an app it can't safely move,
rolling its ECS service back to the previous task definition if anything fails partway
through (RECOMMENDED item 4, security review).
"""
import logging
import uuid

from django.core.management.base import BaseCommand

from api.models import Application

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Backfill host-mode routing onto already-deployed ECS apps whose infra is TLS-ready"

    def add_arguments(self, parser):
        parser.add_argument("--infrastructure-id", default=None, help="Limit to one infrastructure")
        parser.add_argument("--dry-run", action="store_true", help="Report eligibility without calling AWS")

    def handle(self, *args, **options):
        from shared.enums.orchestrator import ComputeType

        from api.services.application_deployment_service import (
            ApplicationDeploymentService,
        )
        from api.services.deployment_lock import DeploymentLock

        service = ApplicationDeploymentService()
        apps = Application.objects.filter(
            status='ACTIVE', host_forward_rule_arn__isnull=True,
            infrastructure__compute_type=ComputeType.ECS_FARGATE,
        ).select_related('infrastructure').order_by('id')
        if options["infrastructure_id"]:
            apps = apps.filter(infrastructure_id=options["infrastructure_id"])

        if options["dry_run"]:
            self._dry_run(service, apps)
            return

        # RECOMMENDED item 4 (security review): one worker id per run, matching
        # run_worker.py's own pattern — every app this run touches is attributed to the
        # same owner in Redis, distinguishable from an ordinary deploy/rollback worker's
        # lock without needing a new naming scheme.
        worker_id = f"backfill-{uuid.uuid4().hex[:8]}"
        lock = DeploymentLock()
        migrated = skipped = failed = locked = 0

        for app in apps:
            if not lock.acquire(str(app.id), worker_id):
                # An ordinary deploy or rollback of this app is in flight — never race it.
                # Left for a later run: this command is idempotent and safe to re-run.
                locked += 1
                logger.info("backfill_host_routing: %s (%s) is locked by another deploy/rollback — skipping", app.id, app.name)
                continue
            try:
                did_migrate, reason = service.backfill_host_routing(app)
            except Exception:
                logger.exception("backfill_host_routing: unexpected failure for %s (%s)", app.id, app.name)
                failed += 1
                continue
            finally:
                lock.release(str(app.id), worker_id)

            if did_migrate:
                migrated += 1
                self.stdout.write(f"migrated {app.id} ({app.name})")
            elif reason == "failed_rolled_back":
                failed += 1
                logger.error("backfill_host_routing: %s (%s) failed and was rolled back", app.id, app.name)
            else:
                skipped += 1
                logger.info("backfill_host_routing: skipped %s (%s): %s", app.id, app.name, reason)

        self.stdout.write(self.style.SUCCESS(
            f"backfill_host_routing: migrated={migrated} skipped={skipped} "
            f"locked={locked} failed={failed}"
        ))

    def _dry_run(self, service, apps) -> None:
        """RECOMMENDED item 4: actually evaluate eligibility (including the live ALB
        listener check) rather than just naming the apps that would be considered — never
        calls ECS/ALB mutating APIs, and takes no lock, since nothing is written."""
        eligible = ineligible = 0
        for app in apps:
            try:
                is_eligible, reason, _ctx = service.evaluate_backfill_eligibility(app)
            except Exception:
                logger.exception("backfill_host_routing --dry-run: failed to evaluate %s (%s)", app.id, app.name)
                ineligible += 1
                continue
            if is_eligible:
                eligible += 1
                self.stdout.write(f"[dry-run] would migrate {app.id} ({app.name})")
            else:
                ineligible += 1
                self.stdout.write(f"[dry-run] would skip {app.id} ({app.name}): {reason}")

        self.stdout.write(self.style.SUCCESS(
            f"backfill_host_routing --dry-run: eligible={eligible} ineligible={ineligible}"
        ))
