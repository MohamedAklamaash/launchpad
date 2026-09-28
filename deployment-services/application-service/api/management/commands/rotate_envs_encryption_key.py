"""H1: move every Application.envs row onto the newest APP_ENVS_ENCRYPTION_KEYS entry.

Run this after prepending a new key to APP_ENVS_ENCRYPTION_KEYS and before removing an
old one — MultiFernet decrypts under any configured key, but only rotating (not simply
adding a new key) actually gets old rows off the key being retired.

Idempotent: a row already encrypted under the newest key is left untouched (checked with
that key alone, not MultiFernet, so "already current" means current, not "decryptable by
something"). Safe to re-run, including mid-rotation after a partial failure.

Concurrency-safe by construction (R2, security review): rotates ciphertext directly with
`MultiFernet.rotate()` — decrypt-then-re-encrypt of the same plaintext bytes under the
newest key, never a decrypt-to-dict-then-json-re-encrypt round trip — and writes with a
compare-and-swap (`UPDATE ... WHERE envs = <value this process just read>`). A real
request can change an application's envs between this command's read and its write; a
blind `Application.objects.filter(pk=...).update(envs=<what we decrypted>)` would silently
overwrite that concurrent change with stale data (a lost update). The CAS instead affects
zero rows when the value changed underneath it, which this command counts and reports
rather than papering over — re-run to pick up whatever the CAS skipped.

Reads and writes in fixed-size batches ordered by id (keyset pagination), not one
`SELECT * FROM ...` fetching every row into memory at once.
"""
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from api.fields import multifernet
from api.models.application import Application

BATCH_SIZE = 500


class Command(BaseCommand):
    help = "Re-encrypt Application.envs rows under the newest APP_ENVS_ENCRYPTION_KEYS entry"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report counts without writing anything")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        keys = settings.APP_ENVS_ENCRYPTION_KEYS
        if not keys:
            raise CommandError("APP_ENVS_ENCRYPTION_KEYS is not configured")
        newest = Fernet(keys[0])
        multi = multifernet()

        table = Application._meta.db_table
        rotated = current = empty = failed = changed_concurrently = total = 0
        after_id = None

        while True:
            rows = self._fetch_batch(table, after_id)
            if not rows:
                break
            for row_id, raw in rows:
                total += 1
                after_id = row_id
                if raw is None:
                    empty += 1
                    continue
                if self._already_current(raw, newest):
                    current += 1
                    continue
                outcome = self._rotate_one(table, row_id, raw, multi, dry_run)
                if outcome == "rotated":
                    rotated += 1
                elif outcome == "changed_concurrently":
                    changed_concurrently += 1
                else:
                    failed += 1

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(
            f"{prefix}rotated={rotated} already_current={current} empty={empty} "
            f"undecryptable={failed} changed_concurrently={changed_concurrently} total={total}"
        ))
        if changed_concurrently and not dry_run:
            self.stdout.write(self.style.WARNING(
                f"{changed_concurrently} row(s) were modified between read and write — "
                "re-run this command to pick them up."
            ))

    def _fetch_batch(self, table, after_id):
        with connection.cursor() as cursor:
            if after_id is None:
                cursor.execute(f"SELECT id, envs FROM {table} ORDER BY id LIMIT %s", [BATCH_SIZE])
            else:
                cursor.execute(
                    f"SELECT id, envs FROM {table} WHERE id > %s ORDER BY id LIMIT %s",
                    [after_id, BATCH_SIZE],
                )
            return cursor.fetchall()

    def _already_current(self, raw, newest: Fernet) -> bool:
        token = raw.encode("ascii") if isinstance(raw, str) else raw
        try:
            newest.decrypt(token)
            return True
        except InvalidToken:
            return False

    def _rotate_one(self, table, row_id, raw, multi, dry_run: bool) -> str:
        """Returns "rotated", "changed_concurrently", or "failed" (doesn't decrypt under
        any configured key — a job for `repair_envs_encryption`, not this command)."""
        token = raw.encode("ascii") if isinstance(raw, str) else raw
        try:
            new_token = multi.rotate(token)
        except InvalidToken:
            self.stderr.write(f"application {row_id}: envs does not decrypt under any configured key — skipped")
            return "failed"
        if dry_run:
            return "rotated"

        new_raw = new_token.decode("ascii")
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {table} SET envs = %s WHERE id = %s AND envs = %s",
                [new_raw, row_id, raw],
            )
            if cursor.rowcount == 0:
                return "changed_concurrently"
        return "rotated"
