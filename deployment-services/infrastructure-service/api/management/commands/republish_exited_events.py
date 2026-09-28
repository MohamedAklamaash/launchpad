"""Republish infrastructure.exited for every Infrastructure that completed exit before
this event existed (H2 — see plan/H-hardening.md).

Complete Exit only started publishing this event once H2 shipped; any infrastructure whose
`exited_at` was set by an older build never sent one, so application-service's read-model
mirror never learned about it. Idempotent and safe to run repeatedly: the consumer applies
a first-wins latch, so republishing an infrastructure that's already mirrored is a no-op.
"""
from api.models.infrastructure import Infrastructure
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Republish infrastructure.exited for every already-exited Infrastructure"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what would be republished without publishing")

    def handle(self, *args, dry_run, **options):
        from api.messaging.producer.producer import infra_producer

        infras = Infrastructure.objects.filter(exited_at__isnull=False).order_by("id")
        count = 0
        if not dry_run:
            # publish() self-connects lazily and swallows a connect failure (it just
            # leaves the message buffered and returns), which would make this one-shot
            # command report "republished N" while nothing actually reached the broker.
            # Connect explicitly up front so an unreachable broker fails the command
            # loudly instead of silently losing every message.
            infra_producer.connect()
        for infra in infras.iterator():
            count += 1
            if dry_run:
                self.stdout.write(f"[dry-run] would republish {infra.id} (exited {infra.exited_at})")
                continue
            infra_producer.publish_infrastructure_exited(infra.id, infra.exited_at)
            self.stdout.write(f"republished {infra.id}")

        verb = "would republish" if dry_run else "republished"
        self.stdout.write(self.style.SUCCESS(f"republish_exited_events: {verb} {count} infrastructure(s)"))
