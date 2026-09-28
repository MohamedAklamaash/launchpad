"""H2 (hardening) RECOMMENDED 2 — see plan/H-hardening.md: recover from a bad or forged
`infrastructure.exited` event on application-service's own read-model mirror, without
touching the database directly (no raw SQL).

Refuses unless infrastructure-service's own row (the source of truth for `exited_at`)
confirms the infrastructure is NOT exited there — this command clears a WRONG mirror, it
never overrides a real exit. Every outcome is logged: who ran it, which infrastructure,
and (on a successful clear) what the stale value was.
"""
import getpass
import logging
import os
import socket

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


def _operator_identity() -> str:
    """Best-effort "who ran this" for the audit log line — a management command has no
    authenticated user, so this is the closest available identity: the OS user and host
    the operator's shell is actually running as."""
    user = os.environ.get("USER") or os.environ.get("USERNAME")
    if not user:
        try:
            user = getpass.getuser()
        except Exception:
            user = "unknown"
    try:
        host = socket.gethostname()
    except Exception:
        host = "unknown"
    return f"{user}@{host}"


class Command(BaseCommand):
    help = "Clear a bad/forged Infrastructure.exited_at on this service's read-model, after confirming with infrastructure-service that it is not actually exited"

    def add_arguments(self, parser):
        parser.add_argument("--infrastructure-id", required=True, help="Infrastructure UUID to clear")
        parser.add_argument("--confirm", action="store_true", help="Required — this command refuses to run without it")

    def handle(self, *args, **options):
        from api.models.infrastructure import Infrastructure
        from api.services.exit_status_client import (
            ExitStatusLookupError,
            fetch_remote_exit_status,
        )

        infra_id = options["infrastructure_id"]
        operator = _operator_identity()

        if not options["confirm"]:
            raise CommandError("Refusing to run without --confirm")

        infra = Infrastructure.objects.filter(id=infra_id).first()
        if infra is None:
            raise CommandError(f"No local Infrastructure row for {infra_id}")

        if infra.exited_at is None:
            self.stdout.write(f"Infrastructure {infra_id} is not marked exited locally — nothing to clear")
            return

        try:
            remote_exited_at = fetch_remote_exit_status(infra_id)
        except ExitStatusLookupError as exc:
            logger.warning(
                "clear_infrastructure_exited refused for %s: could not confirm status with "
                "infrastructure-service (%s)", infra_id, exc,
                extra={"operator": operator, "infra_id": str(infra_id)},
            )
            raise CommandError(
                f"Refusing to clear: could not confirm exit status with infrastructure-service ({exc})"
            ) from exc

        if remote_exited_at is not None:
            logger.warning(
                "clear_infrastructure_exited refused for %s: infrastructure-service confirms "
                "it IS exited (exited_at=%s) — this is not a bad event",
                infra_id, remote_exited_at,
                extra={"operator": operator, "infra_id": str(infra_id)},
            )
            raise CommandError(
                "Refusing to clear: infrastructure-service confirms this infrastructure has "
                f"genuinely exited (exited_at={remote_exited_at})"
            )

        previous_exited_at = infra.exited_at
        Infrastructure.objects.filter(id=infra_id).update(exited_at=None)
        logger.warning(
            "cleared Infrastructure.exited_at who=%s infra=%s previous_exited_at=%s",
            operator, infra_id, previous_exited_at,
            extra={"operator": operator, "infra_id": str(infra_id), "previous_exited_at": str(previous_exited_at)},
        )
        self.stdout.write(self.style.SUCCESS(
            f"Cleared exited_at for {infra_id} (was {previous_exited_at}) — confirmed by "
            f"infrastructure-service as not exited. Logged as {operator}."
        ))
