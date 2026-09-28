"""Operator override to force-disable a custom domain (H6 — abuse/takedown), bypassing the
owner-only check `CustomDomainService.delete_domain` enforces for the customer-facing API.

Reuses `CustomDomainService._teardown` directly — the same PENDING/VALIDATED ->
DISABLING -> detach -> certificate-delete -> DISABLED path `delete_domain` and every
periodic sweep already use, so an operator-forced disable tears the domain down exactly
the way an owner-initiated delete would. No separate, unaudited teardown code path.
"""
import logging
import uuid

from api.models.custom_domain import CustomDomain, normalize_hostname
from api.services.custom_domain_service import CustomDomainService
from django.core.management.base import BaseCommand, CommandError

audit_logger = logging.getLogger("audit.custom_domain")


class Command(BaseCommand):
    help = "Operator override: force-disable a custom domain (abuse/takedown)"

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--domain-id", help="CustomDomain UUID")
        target.add_argument("--hostname", help="Exact hostname (normalized the same way a claim is)")
        parser.add_argument("--reason", required=True, help="Audit trail: why this domain is being disabled")
        parser.add_argument(
            "--confirm", action="store_true",
            help="Required — without it, nothing is changed and the target is only reported",
        )

    def handle(self, *args, domain_id, hostname, reason, confirm, **options):
        if not reason or not reason.strip():
            raise CommandError("--reason must not be empty")

        domain = self._find_domain(domain_id, hostname)
        infra = domain.infrastructure

        if not confirm:
            self.stdout.write(
                f"[dry-run] would force-disable {domain.hostname} (id={domain.id}, "
                f"status={domain.status}, infrastructure={infra.id}) — pass --confirm to act"
            )
            return

        if domain.status == 'DISABLED':
            self.stdout.write(self.style.WARNING(f"{domain.hostname} is already DISABLED — nothing to do"))
            return

        service = CustomDomainService()
        try:
            service._teardown(infra, domain)
        except Exception as exc:
            # _teardown swallows a failed detach (logs and leaves the row in DISABLING for
            # the next sweep), but a non-AccessDenied certificate-delete failure — or the
            # AssumeRole call above it — re-raises. mark_disabling() already committed by
            # this point, so the row is real state, not a no-op: this outcome must be
            # audited too, same as exit_export_audit.py's "every outcome, not just success".
            domain.refresh_from_db()
            audit_logger.info(
                "custom domain force-disable errored mid-teardown",
                extra={
                    "domain_id": str(domain.id),
                    "hostname": domain.hostname,
                    "infrastructure_id": str(infra.id),
                    "reason": reason,
                    "resulting_status": domain.status,
                    "outcome": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise CommandError(
                f"teardown failed for {domain.hostname} after moving it to DISABLING "
                f"({type(exc).__name__}: {exc}) — retried by the next periodic sweep"
            ) from exc
        domain.refresh_from_db()

        audit_logger.info(
            "custom domain force-disabled by operator",
            extra={
                "domain_id": str(domain.id),
                "hostname": domain.hostname,
                "infrastructure_id": str(infra.id),
                "reason": reason,
                "resulting_status": domain.status,
                "outcome": "ok",
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"{domain.hostname} (id={domain.id}, infrastructure={infra.id}): {domain.status}"
        ))
        if domain.status != 'DISABLED':
            self.stdout.write(self.style.WARNING(
                "teardown did not reach DISABLED on the first attempt (detach or certificate "
                "delete not confirmed) — left in DISABLING for the periodic sweep to retry"
            ))

    def _find_domain(self, domain_id, hostname) -> CustomDomain:
        if domain_id:
            try:
                uuid.UUID(str(domain_id))
            except ValueError:
                raise CommandError(f"{domain_id!r} is not a valid UUID")
            try:
                return CustomDomain.objects.select_related('infrastructure').get(pk=domain_id)
            except CustomDomain.DoesNotExist:
                raise CommandError(f"No custom domain with id {domain_id!r}")

        normalized = normalize_hostname(hostname)
        candidates = CustomDomain.objects.select_related('infrastructure').filter(
            hostname=normalized,
        ).exclude(status='DISABLED')
        count = candidates.count()
        if count == 0:
            raise CommandError(f"No non-disabled custom domain with hostname {normalized!r}")
        if count > 1:
            raise CommandError(
                f"{count} non-disabled custom domains match hostname {normalized!r} — use --domain-id"
            )
        return candidates.get()
