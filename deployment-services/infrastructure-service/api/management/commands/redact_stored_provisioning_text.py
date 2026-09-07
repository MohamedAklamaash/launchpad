"""Re-run the provisioning-text redactor over rows persisted before write-time redaction.

Lossy by design: withheld lines are gone once a row is rewritten, and there is no reverse.
A management command rather than a migration on purpose — a migration freezes the
allowlist it shipped with, whereas this always calls the live redactor. Idempotent: rows
already at a fixed point are skipped.
"""

from api.models.database import Database
from api.models.environment import Environment
from api.services.log_redaction import redact_provisioning_text
from django.core.management.base import BaseCommand

BATCH_SIZE = 200
TARGETS = (
    (Environment, ("logs", "error_message")),
    (Database, ("error_message",)),
)


class Command(BaseCommand):
    help = "Redact Environment.logs/error_message and Database.error_message written before write-time redaction"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing")

    def handle(self, *args, dry_run, **options):
        for model, fields in TARGETS:
            scanned = rewritten = 0
            rows = model.objects.only("id", *fields).order_by("id").iterator(chunk_size=BATCH_SIZE)
            for row in rows:
                scanned += 1
                changes = _redacted_changes(row, fields)
                if not changes:
                    continue
                rewritten += 1
                if not dry_run:
                    model.objects.filter(pk=row.pk).update(**changes)
            verb = "would rewrite" if dry_run else "rewrote"
            self.stdout.write(f"{model.__name__}: scanned {scanned}, {verb} {rewritten}")


def _redacted_changes(row, fields):
    changes = {}
    for field in fields:
        stored = getattr(row, field)
        if not stored:
            continue
        redacted = redact_provisioning_text(stored).text
        if redacted != stored:
            changes[field] = redacted
    return changes
