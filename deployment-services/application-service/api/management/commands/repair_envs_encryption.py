"""H1 R1: repair Application.envs rows corrupted by an old (pre-H1) process overlapping
with the encryption migration or a key rotation — see plan/H-hardening.md Release notes
for the runbook this exists to back up when the stop-the-world window wasn't respected.

Detects two shapes, per row, via a raw cursor (never the ORM, which would raise on read):

  - JSON-quoted Fernet token: a real ciphertext token wrapped in JSON string quotes.
    Django's stock `JSONField.from_db_value` silently returns invalid JSON as the raw
    string instead of raising (`except json.JSONDecodeError: return value`), so old code
    reading an already-migrated ciphertext column got the ciphertext back as a plain
    `str`; a later `save()` re-serialized that `str` as a JSON string, wrapping it in
    quotes. Repaired by unwrapping the quotes and re-rotating the now-bare token under the
    newest key.
  - Plaintext JSON: old code's PATCH handler wrote a real (unencrypted) dict into what is
    now a ciphertext-only column. Repaired by encrypting it.

A row that already decrypts cleanly under a configured key is left untouched and counted
as `ok`. Anything else — a value that is neither valid ciphertext, a JSON-quoted token,
nor a plain JSON object — is reported as `unrepairable` and never written to: this command
never guesses at what a value might have meant.

Same concurrency safety as `rotate_envs_encryption_key`: compare-and-swap writes
(`UPDATE ... WHERE envs = <value this process just read>`), keyset-batched reads.
"""
import json

from cryptography.fernet import InvalidToken
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from api.fields import encrypt_value, looks_like_json_quoted_token, multifernet
from api.models.application import Application

BATCH_SIZE = 500


class Command(BaseCommand):
    help = "Repair Application.envs rows corrupted by an old/new code deploy overlap"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report counts without writing anything")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if not settings.APP_ENVS_ENCRYPTION_KEYS:
            raise CommandError("APP_ENVS_ENCRYPTION_KEYS is not configured")
        multi = multifernet()

        table = Application._meta.db_table
        ok = json_quoted = plaintext = empty = unrepairable = changed_concurrently = total = 0
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

                shape = self._classify(raw, multi)
                if shape == "ok":
                    ok += 1
                    continue
                if shape == "unrepairable":
                    unrepairable += 1
                    self.stderr.write(
                        f"application {row_id}: envs is neither valid ciphertext, a "
                        "JSON-quoted token, nor plain JSON — left untouched"
                    )
                    continue

                outcome = self._repair_one(table, row_id, raw, shape, multi, dry_run)
                if outcome == "changed_concurrently":
                    changed_concurrently += 1
                elif shape == "json_quoted":
                    json_quoted += 1
                else:
                    plaintext += 1

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(
            f"{prefix}ok={ok} json_quoted_repaired={json_quoted} plaintext_repaired={plaintext} "
            f"empty={empty} unrepairable={unrepairable} changed_concurrently={changed_concurrently} "
            f"total={total}"
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

    def _classify(self, raw, multi) -> str:
        # Checked before the "does it just decrypt" test, on purpose: a JSON-quoted token
        # actually decrypts fine as-is (base64's lenient parsing discards the quotes), but
        # that tolerance is an implementation detail of the decoder, not part of Fernet's
        # contract — a quoted value is still a malformed column value and gets reported
        # and repaired, never silently accepted because decrypt happened not to mind.
        if isinstance(raw, str) and looks_like_json_quoted_token(raw, multi):
            return "json_quoted"

        token = raw.encode("ascii") if isinstance(raw, str) else raw
        try:
            multi.decrypt(token)
            return "ok"
        except InvalidToken:
            pass

        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return "unrepairable"

        if isinstance(parsed, dict):
            return "plaintext"
        return "unrepairable"

    def _repair_one(self, table, row_id, raw, shape, multi, dry_run: bool) -> str:
        if shape == "json_quoted":
            inner = json.loads(raw)
            new_raw = multi.rotate(inner.encode("ascii")).decode("ascii")
        else:
            new_raw = encrypt_value(json.loads(raw))

        if dry_run:
            return "repaired"

        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {table} SET envs = %s WHERE id = %s AND envs = %s",
                [new_raw, row_id, raw],
            )
            if cursor.rowcount == 0:
                return "changed_concurrently"
        return "repaired"
