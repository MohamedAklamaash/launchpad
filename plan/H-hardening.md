# H — Hardening follow-ups

**Status:** H1–H7 done (mock-verified) · Found while building and reviewing F0–F6 and
F1b; none was owned by any feature. Each item is one PR, built mock-first, with an
independent security review before merge — same process as the features.

## H1 — Encrypt `Application.envs` at rest

**Status: done** (mock-first; pending independent security review before merge).

`Application.envs` (`application-service/api/models/application.py`) was a plaintext
`JSONField`. F3 (H7) and F6 (H6) were designed around that rather than fixing it; this
item fixes it without touching either design (both already re-derive shape/values from
the live `envs` dict rather than storing a copy, so the field swap is transparent to them).

- Envelope-free field-level encryption with a platform key (`cryptography` Fernet via
  `MultiFernet` so keys rotate without a flag day); key from env, required outside dev.
- Data migration encrypting existing rows; reads accept only ciphertext after migration.
- Every reader of `envs` goes through the model — the deploy path (task definition, k8s
  manifest), rollback, exit inventory, the API (which already returns values to the owner
  only) — so no new plaintext copy appears anywhere.
- Rotation command re-encrypting under the newest key.

### Files

- `application-service/api/fields.py` — `EncryptedJSONField` (a `TextField` subclass:
  DB-level JSON queries on `envs` were never used, confirmed by grep, so giving that up for
  opaque ciphertext costs nothing today) plus the `EnvsDecryptionError` hierarchy
  (`EnvsNotMigratedError`, `EnvsJSONQuotedTokenError`) and the encrypt/decrypt helpers
  `rotate_envs_encryption_key`, `repair_envs_encryption`, and migration `0037` share
  (`multifernet`, `encrypt_value`, `decrypt_value`, `looks_like_json_quoted_token` — all
  public: the security review's first pass had the migration import a `_`-prefixed
  "private" helper from this module, which is now the wrong smell to leave in place for
  three call sites doing the same thing).
- `application-service/api/common/envs_encryption.py` — `load_keys(raw, is_dev)`: parses
  `APP_ENVS_ENCRYPTION_KEYS`, validates every entry is a real Fernet key, refuses the
  derived dev-mode fallback key outside dev mode, and holds that key. No key-shaped literal
  is committed here (see Decision 8) — `DEV_FALLBACK_KEY` is derived at import time from
  `sha256(b"launchpad-dev-only-envs-key")`, a human-readable, clearly-non-secret constant.
  Kept free of Django imports so `core/settings.py` can call it the same way it already
  calls `shared.mode.is_dev_mode`.
- `application-service/api/models/application.py` — `envs` is `EncryptedJSONField` (same
  `default=dict, null=True, blank=True` as before).
- `application-service/api/migrations/0037_encrypt_application_envs.py` — see *Migration*.
  Renumbered twice at rebase as other H-items merged first and claimed the same numbers
  for unrelated changes: `0034` → `0036` (H4 (#92) claimed `0034`/`0035` for a log-group
  backfill) → `0037` (H2 (#93) then claimed `0036` for `Infrastructure.exited_at`).
  Currently depends on `0036_infrastructure_exited_at`.
- `application-service/api/management/commands/rotate_envs_encryption_key.py` —
  compare-and-swap, keyset-batched (see *R2* below).
- `application-service/api/management/commands/repair_envs_encryption.py` — new (see *R1*).
- `application-service/api/apps.py` — `_envs_decryption_canary()`, called from
  `ApiConfig.ready()` (see *RECOMMENDED 2*).
- `application-service/api/repositories/application.py`,
  `api/services/application_service.py` — `.defer('envs')` on the list and delete paths
  (see *RECOMMENDED 2*).
- `application-service/api/views/application.py` — the PATCH view returns a generic 500
  for `EnvsDecryptionError`, not a 400 with the exception text (see *RECOMMENDED 3*).
- `application-service/api/messaging/producer/producer.py` —
  `publish_application_updated` no longer puts `envs` on the `application.updated` event.
  It was a pure plaintext leak onto RabbitMQ with no reader: infrastructure-service's
  `Application` read-model (`infrastructure-service/api/models/application.py`) has no
  `envs` column, and its consumer (`api/messaging/consumer/application_consumer.py`)
  never read the key from the payload it received. Grep of both services for `envs`
  confirmed no other RabbitMQ event, cache, or cross-service read-model ever carried it.
- `application-service/core/settings.py`, `test_settings.py`, `env.example` —
  `APP_ENVS_ENCRYPTION_KEYS` wiring.
- `deployment-services/requirements.txt` — `cryptography==50.0.1` pinned explicitly (was
  already present transitively; `pip-audit -r requirements.txt` is clean).
- `application-service/api/tests/test_envs_encryption.py` — 38 tests, see *Tests*.

### Migration

One migration, two operations in a single (atomic) transaction:

1. `AlterField` changes the column from `jsonb` to `text`. Postgres's own
   `ALTER COLUMN ... TYPE text USING envs::text` cast only changes representation — the
   column holds plain JSON text immediately after this step, not ciphertext.
2. `RunPython` (`encrypt_existing_envs`) walks every row with a raw cursor — not the ORM,
   since by this point in the migration graph the model's field is already
   `EncryptedJSONField`, so `Application.objects` would try to decrypt text that isn't
   ciphertext yet — and replaces the plaintext JSON text with its Fernet ciphertext,
   byte-for-byte (no `json.loads`/`json.dumps` round trip; the cast already guarantees
   valid JSON text, and encrypting it as-is is simpler than reformatting it first).

Because both steps run inside one migration's transaction, there is no committed state,
anywhere, where the column is `text` and holds plaintext. The reverse
(`decrypt_existing_envs`) runs before the `AlterField` reverse (Django unapplies operations
in reverse list order), so the cast back to `jsonb` always sees valid JSON text.

**Rollback:** reversing this migration requires `APP_ENVS_ENCRYPTION_KEYS` to still
include whatever key encrypted each row — do not drop a key from that setting until every
row that could have used it has gone through `rotate_envs_encryption_key`. A *code*
rollback (redeploying the previous release) without also unapplying this migration is
unsafe: the old code's plain `JSONField` would read Fernet ciphertext as if it were the
env dict itself. Roll back code and migration together, in that order (migration first,
then code), or not at all.

This in-place approach (rather than add-column/backfill/rename-column) was chosen because
the whole thing is one atomic transaction on the DB side regardless — a partial failure
rolls back the `ALTER` too, so there's no window where a second migration step could be
required to finish cleaning up. The cost is an `ACCESS EXCLUSIVE` lock on `api_application` for the duration, which is
acceptable for this table's size in a self-hosted control plane; a much larger deployment
should re-evaluate.

### Security review follow-ups

An independent review of the first pass (APPROVE WITH FIXES) found the deploy runbook
above was not actually enforceable, rotation had a lost-update bug, and asked for
defense-in-depth around a wrong key and a few narrower fixes. All addressed; details below,
folded into *Decisions*, *Tests*, and *Release notes*.

**R1 — old/new code overlap can leave the column in a state neither version reads
correctly, and it needs to be detectable and repairable, not just avoided.** Two shapes:

- *A JSON-quoted Fernet token.* Old (pre-H1) code's `envs` field is still
  `django.db.models.JSONField`. Its `from_db_value` swallows a JSON parse failure and
  returns the raw string instead of raising
  (`except json.JSONDecodeError: return value`), so old code reading an already-migrated
  ciphertext column gets the token back as a plain Python `str`. If old code then does a
  full `.save()` (not one scoped with `update_fields` excluding `envs`), `JSONField`'s
  `get_prep_value` re-serializes that `str` as a JSON string — `json.dumps("gAAAA...")` —
  wrapping the token in quotes.

  **Empirical finding, not assumed:** this does *not* actually make the row
  undecryptable. `Fernet.decrypt` calls `base64.urlsafe_b64decode` with its default
  `validate=False`, which silently discards any character outside the base64 alphabet —
  including the two `"` the quoting adds — so `MultiFernet.decrypt()` called directly on
  a quoted token succeeds and returns the original plaintext unchanged. Verified in
  `test_a_json_quoted_token_would_decrypt_if_we_let_it`. This is the opposite of what R1
  assumed ("never decrypts") and is reported here rather than silently built around: the
  fix keeps the detector anyway (see below), but the *severity* of this specific shape is
  lower than originally stated — it is a malformed value that happens to still read
  correctly today, not silent data loss.

  `EncryptedJSONField` still treats a quoted token as an error
  (`EnvsJSONQuotedTokenError`, checked *before* the decrypt attempt in `decrypt_value` —
  not in an `except InvalidToken` branch, precisely so the lenient base64 parsing never
  gets a chance to paper over it) because that leniency is a `base64`/`cryptography`
  implementation detail, not part of Fernet's documented contract, and the stored value is
  malformed regardless of whether it currently happens to decrypt.
- *Plaintext JSON.* Old code's PATCH handler writes a real, unencrypted dict into what is
  now a ciphertext-only column — this one is exactly as serious as it sounds: a genuine
  secret sitting in plaintext. `EncryptedJSONField` already refused to silently accept
  this (Decision 6); R1 is about giving an operator a way to fix it instead of only being
  told it's broken.

  `manage.py repair_envs_encryption` (`--dry-run` supported) detects both shapes with a
  raw cursor (keyset-batched, same as rotation) and repairs them: unwraps + re-rotates a
  quoted token onto the newest key, encrypts a plaintext dict. A row that already decrypts
  cleanly is left untouched (`ok`); anything that is neither valid ciphertext, a quoted
  token, nor plain JSON is reported as `unrepairable` and never written to.

  The runbook (*Release notes*) is rewritten to actually prevent the overlap rather than
  assume a `pkill`-style stop holds: `deployment-worker.service` has `Restart=always`, so
  killing the worker process without `systemctl stop`/`disable` just gets it restarted
  mid-migration by systemd. The rewritten runbook uses `systemctl stop` (and disables the
  unit for the maintenance window) for the worker, stops every web replica, and states
  explicitly that an overlap here is silent, permanent data corruption (until
  `repair_envs_encryption` is run) — not merely a temporary outage. The same discipline
  applies to a rollback (migration + code together, per the *Migration* section above).

**R2 — the rotation command had a lost-update bug.** The original
`rotate_envs_encryption_key` read every row's ciphertext, decrypted it, and wrote the
result back with `Application.objects.filter(pk=app_id).update(envs=decrypted_dict)`. If a
real request (an owner's PATCH, or another rotation run) changed that row's `envs` between
the read and the write, the blind `.update()` would silently overwrite that change with
data decrypted from the stale read — a lost update, invisible to everyone. Fixed:

- Rotates ciphertext directly with `MultiFernet.rotate(raw)` (decrypt-then-re-encrypt of
  the *same plaintext bytes* under the newest key) instead of a decrypt-to-dict →
  `json.dumps` → re-encrypt round trip — one fewer place a formatting difference or a
  second lost update could sneak in.
- Writes with a compare-and-swap: `UPDATE ... SET envs = %s WHERE id = %s AND envs = %s`,
  the last parameter being the exact ciphertext this process just read. Zero rows affected
  means something changed the row since the read; counted as `changed_concurrently` and
  reported, never silently treated as success. Re-running the command picks up whatever it
  skipped.
- Reads in fixed-size batches ordered by `id` (keyset pagination) instead of one
  `SELECT * FROM api_application` pulling every row into memory.

`repair_envs_encryption` uses the same CAS-write, keyset-batch pattern.
`test_rotate_reports_changed_concurrently_and_does_not_clobber_it` simulates the exact
race directly against `Command._rotate_one`.

**R3 — plaintext can remain outside the `envs` column itself.** Three sub-findings:

- *Postgres dead tuples / WAL / replicas / PITR backups.* The migration's `UPDATE`s
  (encrypting existing rows) leave the pre-encryption plaintext in dead tuples until
  vacuumed, and in any WAL segment, streaming replica, or PITR/base backup taken around
  that time. Runbook now includes `VACUUM FULL api_application` immediately after the
  migration (same maintenance window — it takes an `ACCESS EXCLUSIVE` lock, same as the
  migration itself, so there's no additional availability cost beyond extending that
  window) and instructions to treat any backup/WAL archive from before the migration as
  containing plaintext secrets, with an explicit expiry.
- *Pre-fix `application.updated` RabbitMQ messages.* Checked directly rather than assumed:
  `infrastructure-service/api/messaging/consumer/application_consumer.py`'s queue
  (`infrastructure-service.application-events`, bound to the `application_events`
  exchange) is declared with no `x-dead-letter-exchange` argument — grepped the whole repo
  for `dead.letter`/`x-dead`/`dlq` and found only two *unrelated* DLQs (the Redis
  deployment-job queue, `inspect_dlq.py`; the platform-DNS dispatch queue), neither
  bound to `application_events`. A malformed `application.updated` message (the only nack
  path that used `requeue=False`, at `application_consumer.py:67-69`) is therefore
  **dropped by RabbitMQ, not routed anywhere** — there is no `application_events` DLQ for
  `inspect_dlq.py`, or anything else, to purge. **This means the review's specific
  instruction ("inspect+purge with inspect_dlq.py, give exact commands") describes
  infrastructure that does not exist in this codebase** — implemented the corrected,
  narrower finding instead of inventing a command against a nonexistent queue: pre-fix
  `envs` values could still be present in the RabbitMQ broker's own persistent message
  store (durable queue, on disk) for however long messages sat unconsumed, and in any
  backup/snapshot of that broker's data directory — the same class of risk as Postgres's
  WAL, and the runbook says so, with the same mark-and-expire guidance. Reported to the
  reviewer rather than silently built around.
- Both of the above are now explicit runbook steps, not left implicit.

### RECOMMENDED follow-ups (all applied)

1. **`load_keys` refuses the known-weak dev-mode fallback key outside dev.** Originally
   also refused a fixed literal committed in `test_settings.py` for pytest; GitGuardian
   flagged both key-shaped literals in CI (correctly — a key-shaped string in source is a
   secret-scanner hit regardless of intended use). Fixed per Decision 8: `test_settings.py`
   now generates its key at import time (`Fernet.generate_key()`, fresh every process, no
   literal to flag) instead of holding a fixed one, which also means there's no longer a
   fixed "committed test key" to refuse — `_KNOWN_WEAK_KEYS` now holds only the derived
   dev-mode fallback. `load_keys` still raises if that key appears in
   `APP_ENVS_ENCRYPTION_KEYS` and `is_dev` is false.
   `test_dev_fallback_key_is_derived_deterministically` re-derives it independently and
   compares, and `test_settings_key_is_generated_not_a_committed_literal` asserts the test
   key is a valid, distinct Fernet key rather than the fixed string this used to be.
2. **`.defer('envs')` on paths that don't need it, plus a startup canary.** The list
   endpoint (`ApplicationRepository.get_all_for_user`) and delete flow
   (`ApplicationService.delete_application` → `ApplicationRepository.get_by_id(...,
   defer_envs=True)` and `.delete()`) now defer the column, so one row with an
   undecryptable `envs` value can't take down an unrelated list or block deleting the very
   row that needs deleting. `QuerySet.delete()` re-evaluates the exact queryset passed to
   it (`Collector.collect()` forces it to check for cascading relations) — deferring on
   that queryset is what actually matters; the read a caller did earlier doesn't carry
   over to a fresh `.filter(...).delete()` built inside the repository, which is what the
   original code did. `_envs_decryption_canary()` (`api/apps.py`, called from
   `ApiConfig.ready()`) reads one row and tries to decrypt it at process startup: skips
   silently when the table doesn't exist yet or is empty (fresh install / an in-progress
   `migrate` run, whose own `ready()` call happens before that run's migrations apply) or
   when the column isn't migrated yet (`EnvsNotMigratedError` — a schema question, not a
   key question), and raises (crashing startup, logged `CRITICAL`) for any other decrypt
   failure — a wrong or missing key fails at deploy time, not on the first real request.
3. **The PATCH view (`ApplicationUpdateView`) no longer turns `EnvsDecryptionError` into a
   400 with the exception text.** It's a server-side data/key problem, not a bad request.
   Caught before the existing `except ValueError` (which it would otherwise match, being a
   `ValueError` subclass): logs `app id` at `ERROR` and returns a generic 500.
4. **`EncryptedJSONField.to_python` raises `django.core.exceptions.ValidationError`, not
   `EnvsDecryptionError`.** `to_python` is the form/admin validation path
   (`full_clean()`), never the DB read path (`from_db_value` handles that) — Django field
   convention is `ValidationError` for bad input there, not an internal exception
   surfacing as an unhandled 500.
5. **`pip-audit -r requirements.txt`: no known vulnerabilities** (re-run after this pass;
   see *Tests*).

### Decisions

1. **Never derived from `SECRET_KEY`/`JWT_SECRET`.** Those already sign
   sessions/CSRF/the F3 content hash and authenticate service-to-service calls; a
   compromise of one secret must not compromise the other. `APP_ENVS_ENCRYPTION_KEYS` is
   its own env var, comma-separated, newest first.
2. **Key loading follows the `LAUNCHPAD_PLATFORM_PRINCIPAL_ARN` pattern**
   (infrastructure-service `core/settings.py`): required and validated (a real Fernet key)
   outside `MODE=dev`; `MODE=dev` falls back to one fixed, publicly-known key with a loud
   `logger.warning`, never silently.
3. **Not a `JSONField`.** `EncryptedJSONField` is a `TextField` subclass. An encrypted
   value is opaque text, so `filter(envs__key=...)`-style DB-level JSON queries become
   impossible — acceptable because nothing in the codebase does that today (checked by
   grep before deciding).
4. **F3's content hash needs no change.** `deployment_snapshot.snapshot_env` hashes
   whatever `application.envs` returns — always a plain `dict`, since the field decrypts
   before any Python code sees it — so the hash is identical before and after the
   encrypt/decrypt round trip. Verified directly in
   `test_snapshot_hash_is_unchanged_by_the_encrypt_decrypt_round_trip`.
5. **`rotate_envs_encryption_key` idempotency is checked against the newest key alone**
   (a single `Fernet(keys[0]).decrypt`, not `MultiFernet`) — a row already encrypted under
   the newest key is left untouched and reported as `already_current`, so a second run
   writes nothing. Rows are re-encrypted with `QuerySet.update()`, not `.save()`, so
   rotation never bumps `updated_at` or fires model signals.
6. **Post-migration plaintext is a hard error, not a fallback.** `EncryptedJSONField`'s
   read path raises `EnvsDecryptionError` (never returns the raw column value) when
   nothing in `APP_ENVS_ENCRYPTION_KEYS` decrypts it — during normal operation that can
   only mean a key was removed before every row using it was rotated, or tampering. It
   also raises (rather than crashing with an `AttributeError`) if the column value isn't
   even text — the signature of running new code against a database that hasn't had
   migration `0037` applied yet.
7. **Django admin needed its own fix.** `Application` is registered in `api/admin.py`
   with the default `ModelAdmin` (no custom form, no `exclude`). A bare `TextField` would
   have given admin a free-text `CharField`: the widget would render `str(decrypted_dict)`
   and, on any save (even one that never touches `envs`), write that Python-repr string
   back through `get_prep_value` as a JSON *string* — silently turning every admin-touched
   app's `envs` into a `str` for every later reader. `EncryptedJSONField.formfield()`
   returns `forms.JSONField` (exactly what `models.JSONField.formfield()` already does),
   so admin validates/round-trips real JSON and `cleaned_data["envs"]` stays a dict.
   Covered by `test_admin_form_round_trips_a_dict_not_a_string`.
8. **No key-shaped literal committed to the repo.** GitGuardian flagged
   `DEV_FALLBACK_KEY` and `test_settings.py`'s fixed test key in CI — correctly: a valid
   Fernet key sitting in source is a secret-scanner hit whether or not it's meant to
   protect anything real. `DEV_FALLBACK_KEY` is now derived at import time
   (`base64.urlsafe_b64encode(sha256(b"launchpad-dev-only-envs-key"))`) from a
   human-readable, obviously-non-secret constant — every `MODE=dev` process still derives
   the identical key (required: it's a shared fallback), but nothing key-shaped appears in
   source for a scanner to catch. `test_settings.py` generates its key with
   `Fernet.generate_key()` at import time instead of holding a fixed literal — fresh every
   test run, never committed, and `load_keys` no longer needs to special-case it as a
   second "known-weak" key at all (see RECOMMENDED 1).

### Tests (`api/tests/test_envs_encryption.py`, 40 tests; full suite 555 passed)

Round trip through the ORM; ciphertext genuinely on disk (raw SQL read, no plaintext, no
key names); empty dict and `NULL` both round-trip correctly; rotation (old key decrypts,
`rotate_envs_encryption_key` re-encrypts under the newest, re-running is a no-op, removing
the old key afterwards still reads); `--dry-run` reports without writing; the migration's
`encrypt_existing_envs`/`decrypt_existing_envs` functions against a row seeded as
pre-migration plaintext (and its reverse); a post-migration non-ciphertext value raises
`EnvsDecryptionError` on read; `load_keys` startup-failure behavior (missing key outside
dev raises, dev fallback warns, a malformed key is rejected, comma-parsing keeps order);
the F3 snapshot hash unchanged across the round trip; the admin form issue (Decision 7);
no plaintext env value reaches `caplog` through a real `_create_task_definition` call
(real `ECSClient`, mocked boto3 client, matching the pattern in
`test_immutable_image_tag.py`/`test_host_mode_deploy_wiring.py`; a positive assertion that
the env-logging line actually fired comes first, so the negative assertion means
something).

Security review follow-ups, 26 additional tests: a JSON-quoted token raises
`EnvsJSONQuotedTokenError` on read, and a paired test pins the empirical finding that it
would actually decrypt if the check didn't run first
(`test_a_json_quoted_token_would_decrypt_if_we_let_it`); `EnvsNotMigratedError` for plain
JSON *text* — the shape real Postgres actually produces through Django's connection for a
still-`jsonb` column, confirmed empirically (`test_not_migrated_error_for_plain_json_text`)
after the first version of this check, based on a wrong assumption about what type
`from_db_value` would receive, made `manage.py migrate` itself unable to run (see the
*Migration verified against real Postgres* section) — plus a defensive fallback test for
the originally-assumed (and now confirmed unreachable in practice) non-string-type case;
`repair_envs_encryption` repairing a quoted token, a plaintext dict, leaving a healthy row
untouched, reporting `unrepairable` without writing, and `--dry-run`; the rotation
command's CAS behavior under a simulated concurrent write
(`test_rotate_reports_changed_concurrently_and_does_not_clobber_it`) and that it uses
`MultiFernet.rotate()` rather than a decrypt/json/re-encrypt round trip; `load_keys` refusing the derived dev-fallback key outside dev, that the fallback
re-derives deterministically, and that `test_settings.py`'s key is a freshly generated,
valid Fernet key rather than a fixed literal (Decision 8); delete and list both
succeeding against a corrupted row (`.defer('envs')`, with `DeploymentLock` stubbed so
the delete path has no live Redis dependency — CI runs application-service tests with no
Redis available), and that a deferred field still raises on the lazy load if actually
accessed; the startup canary skipping an empty table, skipping a pre-migration value (both
mocked and, separately, a real plain-JSON-text row exercising the actual bug above),
passing on a healthy row, and raising + logging `CRITICAL` on a wrong key; the PATCH view
returning a generic 500 (not 400 with `str(e)`) with the app id logged; `to_python` raising
`ValidationError` for bad input and passing `None`/dicts through unchanged.

Existing suites re-run as regression evidence rather than duplicated: `test_rollback.py`
(38 tests, several asserting a seeded secret never appears in rollback previews/history)
and `test_exit_inventory.py` (asserts the same for the exit inventory) both still pass
unchanged against the new field.

**Verification run (final, after rebasing onto H2/H3/H4/H5 on `main` and renumbering the
migration to `0037`):** application-service `pytest -q` — 555 passed (grew across three
rebases — 447 → 471 → 505 → 555 — as H2/H4/H5's own unrelated tests landed on `main`; this
change's own tests stayed at 40 throughout). infrastructure-service `pytest -q` — 880
passed (unaffected; confirms the read-model/consumer have no `envs` dependency).
gateway-service `pytest -q` — 84 passed (unaffected; grew from 52 as H3/H5's own tests
landed, also unrelated to this change). `ruff check deployment-services gateway-service` —
clean. `python -m compileall` (both services) — clean. `iam_policy/generate.py --check` —
clean (untouched by this change; run per `CLAUDE.md`'s standing CI list). `manage.py
makemigrations api --check` — no changes detected (migration `0037`, depending on H2's
`0036_infrastructure_exited_at`, matches what Django would generate — re-verified at each
of the two renumberings). `pip-audit -r requirements.txt` — no known vulnerabilities
(re-run after this pass).

**CI (PR #94) caught two more issues local runs hadn't hit:** a `Tests (deployment-services)`
failure and a GitGuardian secret-scan hit — neither reproducible with the local scripted
env-var setup used throughout this file, both real.

- `test_delete_application_succeeds_when_envs_is_corrupted` connected to `localhost:6379`
  and failed with `redis.exceptions.ConnectionError` — CI's runner has no Redis.
  `ApplicationService.delete_application` acquires `DeploymentLock` (Redis-backed) before
  deleting; every other test in this suite that exercises that path already stubs it
  (`test_app_delete_custom_domain_cleanup.py`), this one didn't. Fixed the same way:
  `monkeypatch.setattr("api.services.application_service.DeploymentLock", MagicMock())`.
  Re-ran the whole file, and the full application-service suite, with
  `REDIS_HOST=127.0.0.2` (a non-routable address, so any live connection attempt fails
  immediately rather than hanging) — all pass, proving no live Redis dependency remains.
- GitGuardian flagged `DEV_FALLBACK_KEY` and `test_settings.py`'s fixed key as committed
  secrets. Fixed per Decision 8 above (derive the dev key from a non-secret constant,
  generate the test key at import time) — no key-shaped literal remains anywhere in the
  diff.

**Migration verified against real Postgres, not just SQLite — twice, and the second pass
found a real bug the first pass and the whole SQLite-backed suite could not have caught.**
pytest never exercises the migration directly (`conftest.py` builds tables from model
state, `MIGRATION_MODULES = {"api": None}`) — the unit tests above call its
`encrypt_existing_envs`/`decrypt_existing_envs` functions directly against a SQLite-backed
`TextField`, which proves the encryption logic but not the Postgres `jsonb`↔`text` cast,
and SQLite has no native jsonb type at all to diverge from in the first place.

First pass (before the security review, migration numbered `0034` at the time): ran
`migrate api 0033` (full prior history), inserted three rows via raw SQL matching that
schema (`jsonb`, one with a seeded secret, one `{}`, one `NULL`), ran `migrate api 0034`,
confirmed via `psql` that the column is now `text`, every non-null value is Fernet
ciphertext, and neither the secret nor any key name appears anywhere in the table;
confirmed via the ORM that all three rows decrypt correctly; ran `migrate api 0033`
(reverse) and confirmed via `psql` the column is `jsonb` again holding the original
plaintext JSON, byte-identical to what was seeded.

Second pass (after the security review's `_envs_decryption_canary`, and after renumbering
to `0036` post-H4): re-ran the same sequence against a fresh container, migrating through
H4's own `0034`/`0035` first. `manage.py migrate api 0036` **crashed before applying
anything**, in `_envs_decryption_canary()` (`ready()` runs before the requested command,
including `migrate` itself), with `EnvsDecryptionError`, not the `EnvsNotMigratedError`
the canary was written to expect. Root cause: `EnvsNotMigratedError`'s original detection
(`not isinstance(raw, (str, bytes))`) assumed a still-`jsonb` column would hand
`from_db_value` a Python `dict` — checked directly with a standalone `psycopg2` connection
and that assumption is correct for psycopg2 on its own, but **not through Django's
connection**: confirmed with `django.db.connection.cursor()` against the same row that raw
`jsonb` comes back as plain JSON *text*, because Django's own `JSONField.from_db_value`
does its own `json.loads` and Django's postgres backend doesn't rely on (or leaves disabled)
psycopg2's default jsonb-to-object typecasting. `decrypt_value` therefore took the "is
text, try to decrypt it" branch on a genuinely pre-migration row and raised the generic
`EnvsDecryptionError` the canary treats as a real key failure — meaning **the canary as
originally written would have made it impossible to ever run this migration**: `migrate`
itself invokes `ready()` before touching the schema, so it would crash on step 3 of the
runbook every time, on every database that had any data in it. Fixed by changing the
detection to "is this valid JSON text, not ciphertext" (`_is_plain_json`) rather than "is
this the wrong Python type" — this also correctly folds in R1's plaintext-corruption case,
which is the same observable shape and needs the same "not a key problem" treatment (see
the updated `EnvsNotMigratedError` docstring in `api/fields.py`). Re-ran the full sequence
after the fix: `migrate api 0035` → seed → `migrate api 0036` succeeds, ciphertext on disk,
ORM decrypts correctly, reverse to `0035` restores the original plaintext `jsonb`.
Regression test: `test_envs_decryption_canary_skips_a_real_pre_migration_row` (seeds a
plain-JSON-text value, no mocking, so it exercises the real code path the mocked
`test_envs_decryption_canary_skips_pre_migration_rows` does not). Container discarded
after each pass.

(The migration was renumbered a third time, to `0037`, after H2 (#93) also landed and
claimed `0036` for `Infrastructure.exited_at` — an unrelated model, no further conflict
with the above; `makemigrations api --check` re-verified clean at each renumbering.)

This is flagged explicitly for the reviewer: it is the kind of bug that only manifests
against the real database engine and only at the exact moment the migration itself runs,
and the project's own SQLite-backed test harness cannot exercise it — real-database
verification before merge is not optional for this change.

**Rotation and repair's compare-and-swap also verified against real Postgres, not just
SQLite.** The CAS `UPDATE ... WHERE id = %s AND envs = %s` passes `id` back as whatever a
raw cursor returned it — a `uuid.UUID` object on Postgres, not the hex string SQLite uses —
and psycopg2 needs an adapter registered to bind a `UUID` object as a query parameter.
Rather than assume Django's postgres backend registers one (it does,
`psycopg2.extras.register_uuid()`, in `get_new_connection`), checked directly: seeded three
rows via the ORM under one key, ran `manage.py rotate_envs_encryption_key` against a real
Postgres database with `(NEW, OLD)` configured — `rotated=3`; ran it again — `rotated=0
already_current=3 changed_concurrently=0`; wrote plain JSON into one row via `psql`, ran
`manage.py repair_envs_encryption` — `plaintext_repaired=1`, confirmed via `psql` that the
row is ciphertext again. All against the id-as-`UUID`-object path this branch had not
previously exercised outside SQLite.

### Release notes

- **New required env var:** `APP_ENVS_ENCRYPTION_KEYS` on application-service, outside
  `MODE=dev`. Generate with
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
  Startup fails immediately (before serving traffic) if unset outside dev — same failure
  mode as a missing `LAUNCHPAD_PLATFORM_PRINCIPAL_ARN`.
- **Migration is stop-the-world — a `pkill`-style stop is not enough, and an overlap is
  silent, permanent data corruption, not merely an outage (R1).** Old code (`JSONField`)
  reading the post-migration `text` column gets `json.loads(<fernet token>)`, which fails
  and (per `JSONField.from_db_value`) hands the app the raw ciphertext string as if it were
  the value — old code's business logic then does whatever it does with a string where it
  expected a dict, and a subsequent full `.save()` writes a JSON-quoted version of that
  string back (see *R1* above: this specific shape happens to still decrypt, by accident
  of lenient base64 parsing, but is still detected as an error and worth avoiding). New
  code reading the still-`jsonb` pre-migration column gets a `dict`/`list` handed to
  `decrypt_value`, which raises `EnvsNotMigratedError` (a subclass of `EnvsDecryptionError`)
  — every request touching an `Application` breaks until the migration finishes. Runbook:
  1. `systemctl stop deployment-worker` **and** `systemctl disable deployment-worker` for
     the maintenance window — `deployment-worker.service` has `Restart=always`; a bare
     `pkill run_worker` gets the process restarted by systemd within `RestartSec=10`,
     re-opening the overlap window mid-migration. Stop every web replica the same way
     (whatever process manager/orchestrator runs gunicorn in that environment — no web
     systemd unit is checked into this repo to name here).
  2. Set `APP_ENVS_ENCRYPTION_KEYS` in the environment the migration will run from — the
     `RunPython` step needs it to encrypt existing rows, and it imports `api.fields`, so
     run it from the new release's checkout.
  3. Run `migrate`.
  4. `VACUUM FULL api_application` (R3) — the migration's `UPDATE`s leave pre-encryption
     plaintext in dead tuples until vacuumed. Takes its own `ACCESS EXCLUSIVE` lock; run it
     in the same window as the migration rather than adding a second one later. (`VACUUM
     FULL` also rewrites the table, so this is the point to budget for that on a large
     table, not just the migration's own lock.)
  5. Re-enable and start `deployment-worker`, then start web replicas, then
     `systemctl enable deployment-worker` again.
  6. If step 1 wasn't followed exactly (an overlap did happen): run
     `manage.py repair_envs_encryption --dry-run` first, then without `--dry-run`, before
     resuming normal traffic — do not assume the stale window "just" caused errors, since
     R1's second shape (a real PATCH landing during the overlap) writes genuine plaintext.

  Same discipline for a *rollback*: unapplying this migration needs the same stop, and
  rolling back the code without also unapplying the migration is unsafe in the same way
  the *Migration* section above describes — do not do one without the other.
- **Mark pre-migration backups as containing plaintext (R3).** Any Postgres base backup,
  WAL archive, or logical dump taken before this migration ran holds every application's
  `envs` values in plaintext (jsonb), regardless of what the live table looks like now.
  Tag them, apply an explicit expiry no longer than the org's standard secret-rotation
  window, and don't restore from one without re-running `repair_envs_encryption` (or,
  simpler: re-running the whole migration) against the restored copy before it serves
  traffic.
- **`application.updated` RabbitMQ messages published before this fix (R3) — corrected
  finding.** The security review asked for an "inspect and purge" step against an
  `application_events` DLQ via `inspect_dlq.py`. That DLQ doesn't exist:
  `infrastructure-service`'s `application_consumer.py` declares its queue with no
  `x-dead-letter-exchange`, so a nacked malformed message
  (`application_consumer.py:67-69`) is dropped by RabbitMQ, not stored anywhere;
  `inspect_dlq.py` operates on an unrelated Redis queue (the deployment-job DLQ). A
  pre-fix `envs` value can still be present in the RabbitMQ broker's own persistent
  message store for however long a message sat unconsumed, and in any snapshot/backup of
  that broker's data directory — treat those the same as Postgres WAL/backups above
  (mark, expire); there is no purge command to run against a queue that isn't there.
- **Key rotation runbook (R2: now lost-update-safe):** prepend the new key to
  `APP_ENVS_ENCRYPTION_KEYS`, redeploy, run `manage.py rotate_envs_encryption_key`
  (`--dry-run` first to preview counts), confirm `rotated=0 already_current=<total>
  changed_concurrently=0` on a second run — if `changed_concurrently` is nonzero, re-run
  again before removing the retired key — then remove the retired key on the next deploy.
  Concurrent traffic during rotation is safe by design (R2); this does not need the
  stop-the-world treatment the migration does.
- **Risks / follow-ups for the security reviewer:** (1) the migration's `ACCESS EXCLUSIVE`
  lock duration on `api_application`, and now also `VACUUM FULL`'s, scale with row count
  and average `envs` size — fine today, worth a `pg_stat_activity` check before running
  against a much larger table later; (2) `rotate_envs_encryption_key` and
  `repair_envs_encryption` batch reads (keyset pagination, 500 rows/batch) but still
  re-check every row in the table on every run — fine at current scale, would want a
  "since last run" watermark before it isn't; (3) the startup canary
  (`_envs_decryption_canary`) checks exactly one row, chosen by no particular ordering
  (`LIMIT 1` with no `ORDER BY`) — if a table has one corrupted row among many, this canary
  has no guarantee of finding it; it catches "the configured key doesn't work at all" far
  more reliably than "one specific row is corrupted", which is what
  `repair_envs_encryption --dry-run` is for.

## H2 — Enforce exit in application-service

**Status:** done (mock-verified; no real-AWS surface — this is a read-model/authz gate, not
an AWS-touching change).

F6 Decision 14: after *Complete exit* infrastructure-service refuses reprovision/config
changes, but application-service still deploys and creates apps — its read-model never
learns `exited_at`. Publish an `infrastructure.exited` event (or extend the readiness
contract), mirror `exited_at`, refuse deploy / rollback / app create / webhook deploys /
custom-domain attach with a clear error.

### Files

- `infrastructure-service/api/messaging/producer/producer.py` (`publish_infrastructure_exited`,
  `declare_queue` wiring), `api/views/exit_export.py` (publishes after `exited_at` is set,
  and again on the already-exited 409 branch), `api/management/commands/
  republish_exited_events.py`, `api/services/exit_republish.py` (periodic-tick backstop),
  `api/management/commands/run_worker.py` (the tick), `api/views/infrastructure_internal.py`
  + `api/routes.py` (exit-status lookup for the clear command).
- `application-service/api/models/infrastructure.py` (`exited_at` + migration 0036),
  `api/messaging/consumers/infrastructure.py` (`InfraExitedEventConsumer`), `api/apps.py`
  (consumer thread wiring), `api/services/exit_enforcement.py`
  (`InfrastructureExitedError` + `require_not_exited`), `api/services/application_service.py`
  (create/update), `api/services/rollback_service.py` (rollback/resume-auto-deploy),
  `api/services/application_sleep_service.py` (sleep/wake), `api/views/application.py`
  (deploy/retry/create/update/sleep/wake views + webhook + error mapping),
  `api/views/custom_domains_internal.py` (attach), `api/services/application_deployment_service.py`
  (`_abort_if_exited`, `evaluate_backfill_eligibility`),
  `api/management/commands/backfill_host_routing.py`,
  `api/management/commands/tag_existing_app_resources.py`,
  `api/management/commands/run_worker.py` (dequeue-time re-check in
  `execute_deploy_job`/`execute_rollback_job`),
  `api/management/commands/clear_infrastructure_exited.py`, `api/services/exit_status_client.py`.
- `shared/resilience/amqp.py` (`ResilientPikaProducer.declare_queue`),
  `shared/middleware/authentication.py` (`EXEMPT_EXACT_PATHS` addition).
- `launchpad-frontend/types/application.ts` (`infrastructure_exited`),
  `app/dashboard/applications/[id]/page.tsx` (banner + hidden actions, incl. sleep/wake),
  `app/dashboard/applications/new/page.tsx` (infra picker excludes exited infras).

### Decisions

1. **A dedicated `infrastructure.exited` event, not an extension of
   `host_readiness_updated`, and no version counter.** `HostReadinessEventConsumer` treats
   `host_readiness_version` as "higher wins" — exactly right for a value that moves in both
   directions (dns_synced/https_ready flip on every terraform apply). `exited_at` is a
   one-way latch: infrastructure-service's own field is set once by the exit flow and never
   cleared. Reusing the counter would have been actively wrong: a `host_readiness_updated`
   publish that fires *after* exit (the DNS writer's converge loop, a TLS re-check tick)
   would carry a higher version than an exited event minted earlier, and the versioned
   consumer would discard the exited event as "stale" — the exact bug this decision avoids.
   `InfraExitedEventConsumer` instead applies "first exited event wins, never cleared or
   changed by anything after" — the same write-once pattern `upsert_infrastructure` already
   uses for `dns_label`. Verified by `test_infra_exited_consumer.py`: a later event, an
   earlier event, and a duplicate redelivery are all no-ops once set; a subsequent
   `host_readiness_updated` or `infrastructure.created` upsert cannot clear it either.
2. **Published from `infrastructure_complete_exit` in two places**: right after
   `infra.save(update_fields=["exited_at"])` on first success, and again on the
   already-exited 409 branch. The second publish is deliberate self-heal — a lost or
   not-yet-consumed event on the first call costs nothing extra to fix, since the owner
   retrying "Complete Exit" (which lands on the 409) republishes it for free, and the
   consumer's first-wins latch makes a duplicate publish harmless. A `republish_exited_events`
   management command covers infrastructures that exited before this event existed at all
   (real data on `main` predates H2).
3. **Best-effort, never fails the request** — wrapped exactly like
   `api/services/host_readiness.py:publish_host_readiness`: a broker hiccup must not turn a
   successful exit into a 500 (`test_publish_failure_does_not_break_complete_exit`).
4. **`require_not_exited(infra)` is one helper, one exception
   (`api.services.exit_enforcement.InfrastructureExitedError`, `code =
   "infrastructure_exited"`)**, called at every mutating entry point right after the
   infrastructure is fetched — before any other side effect. This matters concretely for
   retry: `ApplicationRetryDeployView` resets ARNs to null and enqueues a cleanup job before
   enqueuing the fresh deploy, so the check runs before that reset, not after
   (`test_retry_view_returns_409_and_never_resets_arns`).
5. **Gated:** app create, app update, manual deploy, retry, rollback, resume-auto-deploy,
   GitHub webhook deploys (ack 200 without enqueueing, same shape as the existing
   `auto_deploy_paused` ack — GitHub must not see a 4xx/5xx and retry forever), custom-domain
   attach (checked against the target `infrastructure_id` in the request body, before calling
   `attach_custom_domain`). **Not gated, deliberately:**
   - App delete and its cleanup worker job — F6's whole point is the customer keeps a
     working account after exit; refusing cleanup would leave orphaned ECS/ALB resources
     dangling forever. If the deployment role is already gone the AWS call fails and the
     job takes the existing retry/DLQ path, same as any other post-exit AWS failure.
   - Custom-domain detach — `complete_exit` itself calls `teardown_for_infrastructure`,
     which detaches every route; gating detach would make exit unable to tear down its own
     domains.
   - `export_inventory` / `application_summary_for_custom_domains` — reads, and the exit
     export must keep working after exit (that is its entire purpose).
   - Runtime logs (`api/views/runtime_logs.py`) — read-only, and the logs are the customer's
     own CloudWatch/cluster data, harmless to read after exit. Not fixed here: if the
     customer has already deleted `LaunchpadDeploymentRole` by the time they call this, the
     AssumeRole fails and it surfaces as the existing 502 `UpstreamError` path — no special
     post-exit case needed, but this is a real gap between "exited" and "role actually
     revoked" worth knowing about.
   - Sleep/Wake — out of scope for this round (not in F6 Decision 14's original list); they
     mutate ECS desired-count but neither creates new resources nor changes deploy
     configuration. Tracked as a follow-up if a real exited customer reports it as
     confusing, not fixed here.
6. **`backfill_host_routing` reports ineligible rather than filtering the queryset.**
   `ApplicationDeploymentService.evaluate_backfill_eligibility` checks
   `application.infrastructure.exited_at` first and returns `(False, "infrastructure_exited",
   None)`. The command's queryset deliberately still includes exited apps so `--dry-run`
   reports them as ineligible instead of silently vanishing from the output — the real run
   never reaches AWS for one either way, since eligibility is re-checked before any mutation.
7. **Worker re-checks at dequeue time**, mirroring the existing `auto_deploy_paused`
   re-check in `execute_deploy_job` for webhook jobs: `execute_deploy_job` and
   `execute_rollback_job` both re-read the app's infrastructure fresh off the DB (the
   read-model mirror, not a value carried in the job payload) and ack-without-running if
   `exited_at` is set — a job queued before exit cannot run after
   (`test_worker_skips_a_{deploy,rollback}_job_when_infra_exited_at_dequeue_time`).
8. **`infrastructure_exited` added to the app detail response** rather than relying on the
   frontend cross-fetching `GET infrastructures/{id}` and reading its `exited_at` — that
   call already exists on the app detail page (for `isOwner`/`compute_type`) but is
   independently permissioned and best-effort (`.catch(() => setInfra(null))`); an invited
   ADMIN whose infra fetch fails would otherwise never see the gate. The field is computed
   from application-service's own mirror (`app.infrastructure.exited_at is not None`), the
   same row the enforcement checks already read, so the two can never disagree.
9. **Additive migration only** (`0036_infrastructure_exited_at`, nullable
   `DateTimeField`, `editable=False`) — no backfill migration needed since the read-model
   is populated by the consumer/republish command, not by a data migration reading
   infrastructure-service's database directly (the two services don't share a database).
   **Migration numbering:** originally landed as `0034` off `0033_custom_domain_route`;
   H3 (#91) and H4 (#92) both merged first and H4 added its own `0034`/`0035`
   (`0034_application_log_group_name`, `0035_backfill_legacy_log_group_name` — see H4's
   own Decisions for its collision note with H1's unmerged `0034_encrypt_application_envs.py`).
   Renumbered to `0036` at rebase time, dependency repointed at `0035_backfill_legacy_log_group_name`.
10. **Permission check always runs before `require_not_exited`, never after.**
    `create_application`/`update_application` originally checked exit state first; fixed
    so a non-member gets `PermissionError` (403) regardless of an infra's exit state,
    matching the order every other gated path (deploy/retry/rollback) already used.
    Checking exit state first would have let anyone who can guess or enumerate an
    infrastructure UUID learn whether it has exited without any access to it — a small
    oracle, closed by ordering the checks the same way everywhere
    (`test_{create,update}_application_checks_permission_before_exit_state`).
11. **`republish_exited_events` connects explicitly before its publish loop, and lets a
    connect failure raise.** `ResilientPikaProducer.publish()` lazily calls its own
    `connect()` when not yet connected, but swallows a connect failure internally and just
    leaves the message in its in-memory buffer — fine for a long-lived process (the next
    publish attempt retries), wrong for a one-shot management command that would otherwise
    exit right after, silently losing every buffered message while printing "republished
    N". `infra_producer.connect()` (which does re-raise) runs first, so an unreachable
    broker fails the command loudly instead of reporting a false success
    (`test_republish_exited_events_fails_loudly_when_the_broker_is_unreachable`).

### Rollout / operator note (superseded in part — see Decision 13 below)

Standard migrate-before-start discipline, not specific to this feature: if
application-service's web/worker/consumer processes start on code that references
`Infrastructure.exited_at` before migration `0036` has been applied, every read of that
column raises `ProgrammingError`, which `InfraExitedEventConsumer` (like every other
consumer in that file) treats as non-transient and nacks without requeue — the event is
lost, not retried.

## Independent security review (post-merge-readiness pass)

Found no BLOCK. Two REQUIRED fixes and four RECOMMENDED, all applied below.

12. **R1 — a job re-checks exit only at dequeue; a deploy/rollback already running when
    exit lands kept mutating.** A deploy/rollback can take minutes (CodeBuild, ECS service
    stabilization, ALB target-health polling) — long enough for Complete Exit to land
    mid-flight. `ApplicationDeploymentService._abort_if_exited(application)` does a fresh,
    single-field DB read of `Infrastructure.exited_at` (never the in-memory
    `application.infrastructure`, which was read once at the top of the call and would
    miss anything that changed since) and is called immediately before every remaining
    AWS/Kubernetes mutation: `_create_ecs_service`'s `ecs.create_service` call (which
    itself branches internally into `update_service` for an already-existing service —
    one checkpoint covers both), `_configure_alb_routing`'s listener-rule creation,
    `_configure_host_routing` (guarded at entry, covering both the host-forward-rule
    create and the downgrade-to-path-mode delete branch), both EKS deploy paths
    (`_deploy_to_eks`, `_rollback_eks`, immediately before `EKSDeployer.deploy`),
    `_rollback_ecs`'s `update_service` call, and `backfill_host_routing`'s own
    `update_service` call (on top of its existing eligibility check, closing the same
    TOCTOU window there too). Raises `InfrastructureExitedError`, which every one of
    these call sites' existing outer `except Exception` already turns into the same
    cleanup-then-FAILED-with-sanitized-message path a build or AWS failure gets — no new
    failure handling needed, and no half-applied state worse than what that path already
    produces. Exit does **not** forcibly drain or cancel a deploy/rollback already past
    its last checkpoint (e.g. mid `_wait_for_service_stable_with_refresh`) — it stops the
    *next* mutation, not an AWS operation already in flight. Tested by actually racing it:
    `test_deploy_aborts_if_infra_exits_mid_deploy_before_ecs_service_call` and
    `test_rollback_aborts_if_infra_exits_mid_rollback_before_update_service` run a real
    mock deploy/rollback through `MockSession`, flip `exited_at` from inside a patched
    `_create_task_definition` (simulating the exit landing mid-pipeline), and assert the
    next AWS call never happens; `test_deploy_abort_cleans_up_resources_already_created`
    confirms the cleanup path still runs.
13. **R2 — the exited event can be lost, and the 409 self-heal needs the owner to act
    again.** Two independent fixes, since either alone leaves a gap:
    - **(a) The consumer's queue is now also declared/bound from the producer side.** A
      topic exchange silently drops a message with no matching binding — if
      application-service's `InfraExitedEventConsumer` has never run once,
      `infrastructure.exited` had nowhere to land before this fix (the "Rollout /
      operator note" above no longer needs the deploy-ordering workaround it used to
      describe). `shared/resilience/amqp.py:ResilientPikaProducer.declare_queue` declares
      the SAME queue with IDENTICAL properties to
      `ResilientPikaConsumer._connect_and_consume`'s own `queue_declare`
      (durable/exclusive/auto_delete, no extra `arguments`) — RabbitMQ closes the channel
      with PRECONDITION_FAILED on any mismatch, which would break the consumer's own next
      reconnect, so `test_declare_queue_args_match_the_consumers_live_declare_call`
      cross-checks against the consumer's actual call rather than a hardcoded copy of its
      kwargs. `InfraEventProducer.connect()` calls it for
      `application-service.infra-exited-events` (a literal string — this queue belongs to
      a different service/codebase this one must not import from), best-effort: a
      declare/bind failure never blocks `connect()` itself, since two more independent
      layers ((b) below, and the 409 self-heal) still exist.
    - **(b) `api/services/exit_republish.py:republish_all_exited_infrastructures()`** runs
      automatically from a periodic, fleet-wide-rate-limited tick in
      infrastructure-service's `run_worker.py` (same `r.set(lock_key, ..., nx=True)`
      pattern as the existing reap/cert-check/custom-domain ticks), initialized to fire
      once immediately at worker startup and every
      `INFRA_EXITED_EVENT_REPUBLISH_INTERVAL_SECONDS` (default 900s) after. An operator no
      longer has to remember to run `republish_exited_events` by hand — it now happens on
      its own, closing the gap for an infra that exited while every worker was down, or
      whose event was lost for any other reason. A tick failure is logged and skipped, not
      raised, so a broker outage never takes down the dispatch loop.
14. **RECOMMENDED 1 — sleep/wake gated too.** Not in F6 Decision 14's original list, but
    they mutate the ECS service (`ecs.update_service` in
    `application_sleep_service.py:sleep_application`/`wake_application`) the same way
    deploy does. Gated at both the view layer (`ApplicationSleepView`/`ApplicationWakeView`,
    409 `infrastructure_exited` after the existing ownership check) and the service layer
    (`require_not_exited(infra)` right after `InfrastructureRepository.get_infrastructure`,
    a fresh read, before either `ecs.update_service` call) — defense in depth, matching
    every other gated action. Frontend hides both buttons when `infrastructure_exited`.
15. **RECOMMENDED 2 — recovery from a bad/forged event, without raw SQL.** A new
    `clear_infrastructure_exited --infrastructure-id <id> --confirm` command on
    application-service. It never trusts its own caller's word for it: it calls a new
    internal-only, X-INTERNAL-TOKEN-authenticated endpoint on infrastructure-service
    (`GET /api/v1/internal/infrastructures/exit-status/?infrastructure_id=<id>`, exempt
    from JWT the same way `custom-domains/disable-for-application` is — fixed literal
    path, id in the query string rather than a path segment, matching that exemption
    list's own "no path-param UUID" rule) and refuses to clear unless that response
    confirms `exited_at` is null there. Unreachable, non-200, or "it IS exited there" all
    refuse — the command clears a wrong mirror, it never overrides a real exit. Every
    outcome is logged (`api.management.commands.clear_infrastructure_exited`, WARNING);
    a successful clear logs the operator identity (OS user@host — a management command has
    no authenticated user) and the stale `exited_at` value it removed.
16. **RECOMMENDED 3 — `time.sleep` inside a pika `BlockingConnection` callback starves its
    heartbeat processing.** `InfraExitedEventConsumer.callback`'s bounded retry-with-backoff
    (an unmaterialized `infra_id` — event ordering, not an error) called `time.sleep(delay)`
    before nacking with requeue, copied from the same pattern already present in every
    other consumer in this file (`InfraEventConsumer`, `InfraUpdatedEventConsumer`,
    `HostReadinessEventConsumer` — all pre-existing, not part of this feature). A bare
    `time.sleep()` blocks the callback thread for up to 30s without pumping the
    connection's own I/O loop, so the broker never sees a heartbeat during that window and
    can drop the connection under load. Fixed in `InfraExitedEventConsumer` specifically
    (the consumer this feature owns) with `ch.connection.sleep(delay)` — pika's own
    documented answer to exactly this, since it blocks for the same duration but keeps
    processing `process_data_events` internally. `MAX_RETRIES` (10, unchanged) already is
    the bounded "give up after N attempts" a real dead-letter queue would also provide, and
    no AMQP DLX exists anywhere in this codebase's consumers to route to (the "DLQ" in
    `inspect_dlq.py` is a separate Redis structure for the Redis-backed deployment job
    queue, unrelated). **Not fixed in the three sibling consumers** — they predate H2, are
    outside this feature's diff, and touching three unrelated consumer classes for a
    pre-existing pattern is a different PR's blast radius; tracked as an H6 follow-up.
    `test_unmaterialized_infra_is_requeued_then_ackable_once_it_exists` now asserts
    `ch.connection.sleep(1)` is called and that the module's `time.sleep` is not.
17. **RECOMMENDED 4 — two bulk/backfill tools re-checked for the same staleness classes.**
    `tag_existing_app_resources` (a one-off cost-attribution tagging backfill) now filters
    its outer `Infrastructure.objects.filter(exited_at__isnull=True)` loop — no reason to
    even attempt `create_boto3_session` against an exited customer's account for a tool
    they have no reason to want re-run. `backfill_host_routing`'s command loop
    `select_related('infrastructure')`s once at the top and can run long across many apps;
    a per-iteration `app.infrastructure.refresh_from_db(fields=['exited_at'])` (both the
    real run and `--dry-run`) closes the window where an infra exits between the queryset
    evaluation and a later app's turn — belt-and-suspenders on top of R1's TOCTOU-safe
    `_abort_if_exited` inside the deploy service itself, which remains the layer that
    actually gates the AWS mutation regardless of what this refresh sees.
    `test_command_skips_a_later_app_whose_infra_exited_mid_run` proves the specific race:
    two apps on one infra from a single queryset evaluation, the infra exits after the
    first is processed, and the second is skipped rather than migrated on stale data.

## H3 — Unauthenticated identity endpoints

**Status: done** (`fix/h3-identity-authz`, merged #91). Hardening follow-ups from
security review below, on `fix/h3-otp-hardening`.

user-service `GET /users/:userId` and `GET /users?q=` (`user.controller.ts`) have no
auth; the gateway exposes search publicly (`gateway-service/app/api/endpoints/user.py`),
so anyone can enumerate users by email. The notification route
`/notifications/user/{user_id}` passes a caller-chosen user id. Audit every
identity-service and notification-service route for the same pattern (id from
path/body, no verified caller). Fix: derive the caller from a verified token; scope
lookups to what the caller may see.

**Decisions**

Root cause: `user-service` and `notification-service` both already carried `JWT_SECRET`
in their env schema (`config/env.ts`) and already used it to gate the Swagger docs
(`middleware/docs-auth.middleware.ts`) — but nothing verified a token on the actual data
routes. Per `CLAUDE.md`, the gateway does not verify JWTs and passes `Authorization`
through unchanged, so once a route reached the gateway with no auth dependency of its
own, it was reachable by anyone. Each service now verifies its own caller locally
(`src/utils/resolve-caller.ts`, one per service — not lifted into `@launchpad/common`,
since that would add `jsonwebtoken` to the shared package's dependency graph and touch
the root lockfile for no reuse benefit; every other JWT check in this codebase, e.g.
`docs-auth.middleware.ts`, is already duplicated per service the same way).

Audit table — every identity-service/notification-service route, before vs. after:

| Route | Before | After |
|---|---|---|
| `GET /users/:userId` (user-service) | No auth; any caller could fetch any user's full profile by id. | Requires a verified token. 403 unless `userId === token.sub` — no admin carve-out (member lists already exist via auth-service's `/invited-users`, scoped to infras the caller owns). |
| `GET /users?q=` (user-service) | No auth; any caller could enumerate users by name/email substring, full profile returned. | Requires a verified token and `q.trim().length >= 3`. Results are scoped to users who share at least one infra with the caller (fetches the caller's own row by `sub`, intersects `infra_id`) — role alone (e.g. "any super_admin") isn't sufficient since every infra creator is a super_admin of their own tenant. Returns only `user_id`, `user_name`, `email`, `profile_url` (never `infra_id`, `role`, `invited_by`, `metadata`). If the caller's own record hasn't replicated yet, returns `[]` rather than erroring. No product feature calls this today (verified against `launchpad-frontend/lib/api/*` and every other service — Django's `application_service.py` constructs a `user_client` but never calls it), so this is forward-looking scoping for an eventual invite-by-search flow, not a fix for a broken caller. |
| `GET /notifications/user/:userId` (notification-service) | No auth; `userId` was fully caller-chosen — anyone could read any user's notifications by guessing/incrementing an id. | Route replaced with `GET /notifications/me` (service and gateway) — no target-user parameter at all; the id comes only from the verified token's `sub`. Nothing called the old shape (same grep), so there was no compatibility reason to keep a path param and compare it to `sub` instead. |
| `POST /auth/update-password` (auth-service) | Not itself in the original ask, but same bug class: `email` came from the request body with no token check at all, despite Swagger claiming `bearerAuth`. `oldPassword` gated *which* account's password changed but not *whose* — any caller who knew (or brute-forced) a valid `email` + `oldPassword` pair could act on it without ever authenticating as that user. | Requires a verified token; the account acted on is `token.sub` (looked up via `InvitedUser.findByPk`, same pattern as the existing token-based `resetPassword`). `email` is no longer accepted in the body at all. `oldPassword` is still required as proof of current-credential possession. |
| `POST /auth/register`, `GET/DELETE /auth/invited-users*`, `POST /auth/revoke` (auth-service) | Reviewed — already correct. | No change. These already verify the token inline per-controller and derive the caller/target scope from it (`RegisterInvitedUser`/`RemoveMemberFromOrg` via `superAdminMiddleware` + infra ownership; `RevokeRefreshToken` via `resolveRevokeCallerId`, fixed in PR #85). This is the pattern the H3 fixes above were matched to. |
| `POST /auth/forgot-password` | **CRITICAL, found during H3 review.** Returned `{ otp }` in the response body unconditionally (the gateway's `ForgotPasswordResponse` model claimed "dev-only" but `PasswordService.requestPasswordReset` never actually gated the echo on `NODE_ENV`) — the same code, the OTP, authenticates via `authenticate-with-otp`/`verify-reset-otp`, so this was a full account-takeover-by-email primitive in every environment including production. Also enumerated accounts: 404 for an unknown email, 200+OTP for a known one. | `requestPasswordReset` never returns the OTP (removed from the return value entirely, not just from the controller) and never throws for an unknown email or an account with no infra — it silently does nothing. `ForgotPassword` always responds `202 { message }` with the same generic body, and doesn't wait for `requestPasswordReset` to finish (fire-and-forget, errors logged server-side only) — so response time doesn't distinguish a known email (DB write + queue enqueue) from an unknown one (nothing). **Second pass (B3):** added a per-email throttle (1/60s, 5/hour, fixed-window Redis counters) on how often a new code can be minted at all — see the `authenticate-with-otp` row below for the rest of B3. The throttle check fails open (logs and proceeds) rather than aborting the request on a Redis error — otherwise a Redis outage would silently disable password reset entirely rather than just the throttle, which would be a worse availability regression than the abuse it's meant to prevent. Known trade-off, not fixed: anyone who knows a user's email can call this 5 times to lock that user out of requesting a reset for an hour — inherent to a per-email throttle with no other identity to key on. |
| `GET/POST /auth/authenticate-with-otp`, `POST /auth/verify-reset-otp` | **Found during the same review; hardened further after a second security pass found the first fix bypassable (`fix/h3-otp-hardening`).** No cap on failed OTP guesses against a 6-digit code (1,000,000 values) — same secret class as the forgot-password leak above, just reached by brute force instead of by reading the response. `authenticate-with-otp` was GET-only, so the OTP sat in the URL (access logs, browser history, proxies) even for the dashboard's own manual-entry form. `generateOTP` used `Math.random()`, not a CSPRNG. A code minted by one flow could be redeemed through the other (no `purpose` on `UserOTP`) — a password-reset code submitted to `authenticate-with-otp` still matched and minted a full session. `createOTP` never invalidated a prior code, so repeated requests stockpiled multiple simultaneously-valid codes. `forgot-password` had no per-email throttle on how often a new code could be minted. | **First pass (now superseded on two points, B1/B2 below):** added a Redis attempt cap keyed per email+purpose, added `POST /auth/authenticate-with-otp`, switched `generateOTP` to `crypto.randomInt`. **Second pass fixes:** (1) *B1 — check-then-act race:* the cap was "read remaining, evaluate the guess, then record on failure" — N concurrent requests could all read "attempts remaining" before any of them recorded one, so N guesses could land in one round. `otpAttempts.claimAttempt` now does one atomic Redis Lua script (`INCR` + conditional `EXPIRE`) that increments *unconditionally, before* the guess is evaluated; the 6th concurrent claim is rejected outright regardless of interleaving (tested under `Promise.allSettled` on 6 concurrent calls — exactly 5 reach the OTP lookup, 1 gets 429). (2) *B2 — invalidation was rolled back:* the cap-exceeded branch's `UserOTP.destroy` ran inside the same `sequelize.transaction` callback that then threw 429 — Sequelize rolls back everything that callback did on a thrown error, so the "invalidated" OTP was silently un-invalidated. The destroy (and the whole cap check) now runs *before* `sequelize.transaction` opens at all, so it commits independently of anything the transactional path does. (3) *B3 — cross-purpose redemption and stockpiling:* added a `purpose` column to `UserOTP` (`'register'` \| `'password-reset'`, `OTP_PURPOSE` in `types/otp-purpose.ts`); both verify paths now filter on it, `createOTP` deletes the user's existing same-purpose OTP before minting a new one, and the attempt-cap key is `otp-attempts:user:<id>` — no purpose suffix — so a register attempt and a reset attempt on the same account draw from one shared 5-guess budget, not two. `login`'s "block until verified" check is scoped to `purpose: 'register'` specifically, so an outstanding reset code no longer locks a user out of normal password login. `requestPasswordReset` now also checks `otpAttempts.shouldThrottleForgotPassword` (1/60s, 5/hour per email, fixed-window Redis counters) and silently no-ops if throttled — the response stays identical either way. (4) *R3 — enumeration via unknown-email 404:* both endpoints returned 404 "User not found" *before* checking the OTP, distinguishing a registered from an unregistered email independent of the guess. Both now claim an attempt (keyed on the normalized email when there's no account to key on) and return the same 400 "Invalid or expired OTP" whether the account exists or the guess was simply wrong. Auth-service's `UserOTP` table has no migration framework (`sequelize.sync()` only creates missing tables — see `memory/auth-service-no-migrations.md`); the `purpose` column is added idempotently at startup via `ALTER TABLE invited_user_otp ADD COLUMN IF NOT EXISTS purpose VARCHAR(255) NOT NULL DEFAULT 'register'` in `db/index.ts`'s `initModels`, run on every boot. Reviewed, not changed: the OTP compare is still a Postgres `WHERE otp = ... AND purpose = ...` equality, not an app-level constant-time compare. With the atomic 5-attempt cap now closing the concurrency loophole that made this worth revisiting, a network-timing side channel against a 6-digit space is not practically exploitable; rewriting the lookup to compare in JS risked breaking multi-infra-invite support (a user can still hold one live register-purpose and one live password-reset-purpose code at once) for a hardening gain with no realistic attack path left. Reviewed, deferred: the coordinator's recommendation to point the email's magic link at a frontend page reading an `#otp=` URL fragment (never sent to any server, including the gateway's access logs) instead of `GATEWAY_SERVICE_URL`'s query string. A frontend page for this already exists (`app/auth/authenticate-with-otp/page.tsx`) but reads `useSearchParams` (query, not fragment) and `DASHBOARD_URL` in notification-service's env is optional and includes a `/dashboard` suffix that would need origin-stripping to build the link correctly — reworking both without a live environment to verify the redirect end-to-end felt riskier than the gain given everything else in this pass; the query-string encoding fix below (still real, still shipped) covers the sharpest edge in the meantime. |
| `POST /auth/register` (its dev-only `otp` echo) | Reviewed — same "return the OTP" shape as forgot-password, but already gated: `RegisterInvitedUser` only sets `body.otp` when `env.NODE_ENV !== 'production'` (see `local-e2e-multitenant-harness.md` memory — this is the local/e2e convenience the harness relies on). The frontend (`inviteUser`) never reads the field even though its type allows it. | No change — production never sees it, and this is a deliberate different case, not an oversight. |
| Every call site that verifies an access token and treats it as an active session (`RegisterInvitedUser`, `ListInvitedUsers`, `RemoveMemberFromOrg`, `UpdatePassword`, `RevokeRefreshToken`'s `resolveRevokeCallerId`, `GetCurrentUser`'s `getUserFromToken`, and the new `resolveCaller` in user-service/notification-service) | **Found during the same review.** `verifyAccessToken` alone only checks signature and expiry — it doesn't reject a token minted for a narrower purpose. `PasswordService.verifyResetOTP` mints a 5-minute token with `scope: 'password_reset'` that still carries the user's real `role`, so a leaked/observed reset token could reach `superAdminMiddleware`-gated actions (invite/remove members), list the holder's invited users, revoke their sessions, or (via the new H3 code) read their profile/notifications — none of which "I just proved I own this email" should grant. | Added `verifySessionToken` (auth-service `utils/handle-token.ts`): wraps `verifyAccessToken` and additionally rejects any payload with a truthy `scope`. Every call site above now uses it (or the equivalent check added to `resolveCaller` in user-service/notification-service) instead of the raw `verifyAccessToken`/`jwt.verify`. `resetPassword` itself is unchanged — it's the one call site that *requires* `scope === 'password_reset'`, the opposite check. **R1, second pass:** the same gap existed one layer further out than the Node services — `deployment-services/shared/utils/jwt.py`'s `decode_jwt` (used by every Django service's `JWTAuthMiddleware`: infrastructure-service, application-service) and `payment-service/api/common/utils/jwt.py`'s copy (used by its DRF `JWTAuthentication`) both accepted any HS256-signed token regardless of `scope` — a leaked reset token could hit any Django-side endpoint too. Both (and the unused third copy in `application-service/api/common/utils/jwt.py`, patched for consistency though nothing imports it) now raise `ScopedTokenRejected(jwt.InvalidTokenError)` when `payload.get("scope")` is truthy, which every existing caller already handles the same way it handles any other malformed/expired token. Also folded in: `docs-auth.middleware.ts` in all three identity services now goes through `verifySessionToken`/`resolveCaller` instead of a bare `jwt.verify`, so a reset token can't view internal API docs either — low severity (no user data there) but the same class of gap, cheap to close once the helper existed. |
| `POST /auth/authenticate-with-otp`, `POST /auth/verify-reset-otp`, `POST /auth/register`'s OTP-mailing job, `POST /auth/forgot-password`'s OTP-mailing job | **R2, found during the second pass.** BullMQ keeps completed/failed jobs in Redis by default — the job payload for both OTP-delivery jobs (`AUTHENTICATE_INVITED_USER_EVENT`, `FORGOT_PASSWORD_EVENT`) carries the OTP in plaintext, so a short-lived secret was persisting in Redis indefinitely after the email had already been sent. Checked notification-service's consumer (`user-event.consumer.ts`) separately: its `logger.info` calls never include the OTP field, and `notificationService.save(...)` (the Mongo notification record) only ever gets `user_id/email/user_name/infra_id/source` — confirmed no other place the OTP gets written to persistent storage or logs. | Both `userAuthenticationQueue.add(...)` calls now pass `{ removeOnComplete: true, removeOnFail: { age: 3600 } }` — gone immediately on success, gone within an hour on failure. |
| `POST /auth/register`'s dev-only `otp` echo (recommended cleanup) | `RegisterInvitedUser` gated the echo on `env.NODE_ENV !== 'production'`, which also echoes it in a real deployed `staging` environment — `!==` "one specific value" is not the same allowlist as "known-safe local/test environments". | Changed to `['development', 'test'].includes(env.NODE_ENV)`. |
| Every access-token verification in auth-service and both `resolve-caller.ts` helpers (recommended hardening) | `jwt.verify(token, secret)` with no `algorithms` option accepts whatever algorithm the token's own header declares, as long as the corresponding key material validates it — a defense-in-depth gap against algorithm-confusion attacks, not something exploited by anything else in this review. | Pinned `{ algorithms: ['HS256'] }` on every `jwt.verify` call in `handle-token.ts` (access and refresh) and both `resolve-caller.ts` files. (Python's `decode_jwt` already pinned `algorithms=["HS256"]` before this change — unaffected.) |
| `GET /users?q=` search (user-service; recommended hardening, no live caller today) | `searchByQuery`'s `LIKE` clauses didn't escape `%`/`_`/`\` in the caller's own query text, so e.g. `q="___"` matched any 3+ character name — wildcards let a query bypass the intent of the minimum-length gate. The shared-infra filter ran as a JS `.filter()` over a capped 50-row LIKE match, which (documented previously) could starve real matches outside that page. A repeated `?q=&q=` query string parses as an array; `.trim()` on it would throw, surfacing as an unhandled 500. | `escapeLikePattern` backslash-escapes all three characters before building the `LIKE` pattern. The infra filter now runs in SQL via `JSON_CONTAINS(infra_id, '"<id>"')`, OR'd across the caller's own infra ids (each validated against a UUID regex before being spliced into the raw SQL literal — a malformed id is dropped, not escaped, since `infra_id` is always a UUID upstream and there's nothing legitimate to preserve). The JS filter stays as a zero-cost safety net over the now-small result set. The controller explicitly rejects `Array.isArray(req.query.q)` with a 400 before it reaches `.trim()`. |
| `GET /auth/authenticate-with-otp` link in the verification email (recommended hardening) | `user-event.consumer.ts` built the link from unencoded `email`/`otp` — an unencoded `+`, `&`, or `#` in an email address could corrupt the query string (truncating or misparsing `otp`) or get mangled by an email client's own link handling. | Both values now go through `encodeURIComponent`. |
| `POST /auth/login`, `/reset-password`, `/refresh`, GitHub OAuth routes, health/docs routes | Reviewed — legitimately unauthenticated (pre-session flows, proven by password/reset-token/refresh-token possession instead) or exempt (health, docs, favicon). | No change. |

Gateway changes were route/model shape only — `gateway-service/app/api/endpoints/user.py`
(new `UserSearchResult` model for the minimal search response), `notification.py` (route
renamed to `/me`, dropping the `user_id` path param this file's own H5 write-up
documented — H5's decisions above are now updated to match), and `auth.py`
(`UpdatePasswordBody` drops `email`, `ForgotPasswordResponse` drops `otp` in favor of a
generic `message`, new `AuthenticateOtpBody` for the POST OTP route). The gateway itself
still does not verify JWTs; enforcement lives entirely in the services that own the data,
consistent with `CLAUDE.md`. `user.py`'s remaining `/{user_id}` path param keeps H5's
`Path(pattern=...)` constraint.

Frontend: `launchpad-frontend/lib/api/auth.ts` — `verifyOtp` switched from GET (query
params) to POST (body); `forgotPassword`'s doc comment updated to describe the new
generic-response behavior (its own code was already fire-and-forget from the frontend's
perspective, so no functional change there). Every other changed route
(`/users`, `/users?q=`, `/notifications/user`, `/notifications/me`, `update-password`)
still has zero frontend callers (re-verified by grep), so nothing else to update there.

CI gap found and fixed in passing: `user-service`'s `test` script was
`echo "No tests yet"` and wasn't run in `.github/workflows/ci.yml` at all. Added real
tests and a `Unit tests (user-service)` CI step (`check-identity` job) alongside the
existing `auth-service`/`notification-service` steps.

**`fix/h3-otp-hardening` (second pass) notes:** no gateway or frontend files changed in
this pass — every fix lived in auth-service, user-service, notification-service, and the
Python `decode_jwt` copies. `payment-service` has no pytest/pytest-django setup of its
own (confirmed: no `pytest.ini`, no `test_settings.py`, its own `venv` doesn't have
`pytest` installed) and CI only runs `compileall`/`ruff` there — `decode_jwt` has no
Django dependency, so its new test (`api/common/utils/test_jwt.py`) runs as a plain
pytest module; run in practice via `deployment-services/venv` (which already has
`pytest`/PyJWT) pointed at `payment-service`'s directory, matching how it'd run in CI if
a pytest step were ever added there — that gap (no pytest CI step for payment-service)
is pre-existing and out of scope here.

**Known residuals from the second pass, not fixed here:**

- `authenticateWithOTP`/`verifyResetOTP` now depend on Redis being reachable
  (`claimAttempt` runs first, unconditionally) — a Redis outage makes OTP login/reset
  unavailable rather than insecure (fails closed, which is the right default for a
  security gate), but it's a new availability coupling neither endpoint had before this
  pass. `requestPasswordReset`'s throttle check is the one place this was changed to
  fail open instead, since its failure mode (silently not sending a reset email) was
  worse than the abuse it guards against.
- The uniform-400 fix (R3) removes the *account-existence* signal but not a *timing*
  one: an unknown email short-circuits before opening a transaction, a known email with
  a wrong guess does not. The 5-attempt cap bounds guesses per email, not probes across
  many different emails. Not asked for (the review asked for "same 400", which is
  done) — noted so it isn't mistaken for full timing parity.
- Django's `JWTAuthMiddleware` (`shared/middleware/authentication.py`) turns *any*
  `decode_jwt` failure — expired, malformed, or now a rejected `scope` — into a 500 with
  the exception text in the response `details`, because it only catches `HttpError`
  before falling through to a generic `except Exception`. Pre-existing behavior, not
  introduced here, but `ScopedTokenRejected`'s message now flows through that same leak.
  Worth a follow-up: catch `jwt.PyJWTError` there and return 401 with no exception text.
- `createOTP`'s same-purpose dedup (B3) means a user invited to a second infra within
  10 minutes of a first, still-unverified invite gets a new code that invalidates the
  first — clicking the first email's link then fails. Deliberate trade-off (one live
  code per purpose, not one per infra) versus a wider guessable surface; flagged since
  it's a UX change from pre-H3 behavior.
- Unrelated one-line finding: `.gitignore`'s `*.log*` rule matches any path containing
  the substring `.log`, which caught a test file named `...service.login.test.ts`
  (renamed to avoid it here) — worth tightening to `*.log` to only match actual log
  files, since the next `*.login.*`/`*.catalog.*` source file will hit the same trap.

## H4 — Truncated-UUID resource names

**Status: done** (`fix/h4-resource-names`).

`CLAUDE.md` forbids UUIDv7 prefixes as uniqueness keys.

- **Target groups** — `f"{slug}-{infrastructure_id[:8]}-tg"`
  (`application_deployment_service.py`). Two infras in one AWS account created within ~65s
  with the same app slug get the same name, and `CreateTargetGroup` on an existing name
  with the same settings **returns the existing group** — app B registers into app A's
  target group. Use a full-id hash (the `Database.module_name()` pattern); existing TGs keep
  their stored ARN, new ones get the safe name.
- **ECS log groups** — `/ecs/{slug}-task` is shared by same-name apps across infras in one
  account (F2 pre-review). Add a per-infra discriminator for new deploys; runtime-logs and
  cleanup read the stored name.
- Review the remaining `[:8]` sites (`naming.environment_name`,
  `app_security_group_name`, `eks_bootstrap` legacy fallback) — they carry a full-id hash
  suffix, so they are not uniqueness keys; confirm and document rather than rename live
  resources.

**Decisions**

1. **Discriminator is per-row (infra id + application id), not per-infra.** The item
   above says "full-id hash" and "per-infra discriminator"; both `target_group_name`
   and `new_ecs_log_group` (`application-service/api/common/naming.py`) hash the
   infrastructure id *and* the application id together
   (`hashlib.sha256(f"{infra_id}:{app_id}")[:8]`, `Database.module_name()`'s pattern —
   full value hashed first, only the digest sliced). An infra-only discriminator would
   still collide on the delete-then-recreate case: `application_cleanup_service.
   _delete_target_group` retries `ResourceInUseException` six times and then gives up,
   so a deleted app's target group can be left behind, still carrying that app's own
   `launchpad:infra`/`launchpad:app` tags; recreating an app of the same name on the
   same infra would then reproduce both the exact same name *and* the exact same
   expected tags, so the ownership check below would wrongly accept the orphan as
   "ours." Binding the hash to the application id as well as the infrastructure id
   means a recreated app (new id) never lands on its predecessor's name at all.
2. **Target group name truncation never touches the hash.** The old formula truncated
   the whole `f"{slug}-{infra_id[:8]}-tg"` string to ALB's 32-char limit, which could
   already cut into the discriminator (or the `-tg` marker) for a long app name —
   a second, independent bug two long-named apps on *different* infras could hit even
   before H4. `target_group_name` reserves the `-{8-hex-hash}-tg` suffix first and
   truncates only the slug to what's left (20 chars). It also re-restricts the slug to
   `[a-z0-9-]`: `app_slug` still admits `.`/`_` for Docker tags, which ALB target group
   names reject.
3. **Never adopt a target group that isn't ours — checked on both AWS response
   shapes.** `ALBClient.create_target_group` compares the existing target group's
   `launchpad:infra`/`launchpad:app` tags (`describe_tags`) against what this call
   would have set, and raises `TargetGroupOwnershipMismatch` on any mismatch or missing
   tag, before returning an adopted ARN. This runs after `DuplicateTargetGroupNameException`
   (AWS's differing-settings case) *and* after a plain create success (AWS's
   same-name/matching-settings case, which returns the pre-existing target group's ARN
   with no exception at all — the literal scenario this item names). A freshly created
   target group trivially passes (we just set those tags in the same call); the check
   only ever rejects an adoption. One extra `DescribeTags` call, paid once per new
   target group's lifetime, not per deploy.
4. **Existing apps keep their stored `target_group_arn` — no rename.**
   `_create_target_group`'s existing reuse-by-stored-ARN branch (same VPC → `describe_target_groups`
   by ARN, `modify_target_group` for the health check, return) is untouched; the new
   hashed name is only ever computed when that branch falls through (no stored ARN, or
   the stored one is gone/wrong-VPC). A currently-running app's target group is never
   renamed or re-created by this change.
5. **ECS log group: same per-row hash, persisted once, read everywhere through one
   function.** `Application.log_group_name` (additive migration, nullable) is set the
   first time `_create_task_definition` runs for a row with no stored name: to
   `new_ecs_log_group(application)` (the hashed name) if the row has never *completed*
   a deploy, or to the *legacy* `ecs_log_group(slug)` string if it has (a pre-H4 app
   whose task definition/log group already exist under the shared name) — either way
   the result is persisted, so every row converges onto `log_group_name` after its very
   next deploy without ever renaming a live resource (the legacy branch persists the
   exact string it already used). Every later deploy/rollback/backfill of that row
   reuses the stored value instead of recomputing it. Every reader —
   `runtime_logs_service._tail_ecs`, `application_cleanup_service._delete_log_group`,
   `exit_inventory._task_definition_json` — now calls one function,
   `ecs_log_group_for(application)` (`application.log_group_name` or the legacy
   fallback), instead of each re-deriving a name independently (the F2 pre-review's own
   RECOMMENDED-but-not-done item). `ECSClient.create_task_definition` takes the
   resolved name as an explicit `log_group=` kwarg, defaulting to `f'/ecs/{family}'`
   only when omitted, so no other caller's behavior changed.
   - "Has completed a deploy before" is answered from `Deployment` history
     (`_has_succeeded_before`: any row with `status=SUCCEEDED`), not
     `application.task_definition_arn` — an earlier draft of this decision used that
     field and was wrong: `ApplicationRetryDeployView` (`api/views/application.py`)
     resets `task_definition_arn` (and `service_arn`/`target_group_arn`/
     `listener_rule_arn`/`runtime_refs`) to give a failed deploy a clean slate before
     re-queuing, on a **live** `Application` row it does not delete. A pre-H4 app that
     succeeded once, later fails an unrelated redeploy, and gets retried through that
     view would otherwise read as "never deployed" and mint a second, hashed log group
     out from under its own history — caught in review, fixed before this shipped, and
     covered by `test_retry_after_failure_does_not_switch_a_previously_deployed_apps_log_group`.
   - `_rollback_ecs`'s own invariant ("no `Application` field is written until every
     AWS call has already succeeded") is not violated by persisting `log_group_name`
     inside `_create_task_definition` before that: the value written is deterministic
     from `(application, has-succeeded-before)` at call time, identical whether or not
     the rollback ultimately succeeds, and the cpu/memory/port fields that invariant
     actually protects are restored separately, after, on failure — see
     `rollback_application`'s `except` block.
6. **F2's B1 stream binding is unaffected and still does the harder half of the
   isolation work.** `test_two_infras_same_account_same_app_name_read_only_own_streams`
   (unchanged) still proves that even two infras sharing a log group can't read each
   other's task logs, because the actual read is scoped to `logStreamNames` derived
   from `list_tasks` on *this app's own* ECS service. H4's log-group split means a
   post-H4 deploy stops sharing a group at all; pre-H4 rows still rely on B1 alone
   until their next deploy.
7. **No IAM change required.** `policy.json`'s `elasticloadbalancing:*` statement
   already covers `DescribeTags`; there's no new policy version and no
   `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF` bump for this PR.
8. **Remaining `[:8]` sites reviewed, not changed (confirmed non-uniqueness-keys):**
   - `infrastructure-service/api/common/naming.py:environment_name` and
     `shared/aws/app_security_group.py:app_security_group_name` both concatenate a
     `[:8]` UUID prefix with a *separate* full-id hash suffix (`unique_suffix`/`suffix`
     — `hashlib.md5(str(id)).hexdigest()[:8]`, never a slice of the id itself); the hash
     is what actually disambiguates two ids sharing a prefix, so the prefix is a
     cosmetic label, not a uniqueness key. Comments added at both call sites.
   - `eks_bootstrap.py:_ingress_group_name`'s `str(infra.id)[:8]` fallback already
     carries an extensive docstring (added when `dns_label` superseded it) explaining
     it's dead in practice — every infra gets a `dns_label` — and is kept only so a
     bootstrap call never raises for the theoretical row that somehow lacks one. Left
     untouched; renaming it would require the same "don't move a live ALB group name"
     care H4 already applies to target groups and log groups, for a code path that
     shouldn't be reachable at all.
   - Application-service migration `0005_infrastructure_name.py`'s `str(infra.id)[:8]`
     backfills a purely cosmetic `Infrastructure.name` display field on this service's
     own read-model copy (no unique constraint, never an AWS resource name). It's also
     a historical, already-applied data migration — not rewritten in place; a comment
     was added explaining why, rather than changing migration history.
   - `infrastructure-service/api/services/cost_service.py`'s `_mock_costs` was already
     correct before this PR: its seeds hash the full `infra.id`/`app.id` and only slice
     the digest, with a comment already citing CLAUDE.md.
   - Grep for every other truncated-id pattern (Redis/cache/lock keys, cluster names,
     bucket names) found nothing else. The `worker_id`/`lock_owner` values built from
     `uuid.uuid4().hex[:8]` (`run_worker.py`, `backfill_host_routing.py`,
     `application_service.py`) are random UUIDv4s, not UUIDv7 — no embedded timestamp,
     not forceable from any row's `created_at` — so they're out of scope for this item.
   - One further residual, deliberately left alone: `ECSClient.create_task_definition`'s
     `family` is still `{slug}-task` (shared across infras sharing a slug — not a
     security boundary, since every ARN Launchpad stores carries the specific
     revision). (`ALBClient.create_target_group`'s wrong-VPC fallback — originally
     noted here as a second residual, `f"{name[:24]}-{int(time.time()) % 10000}"` — was
     fixed in the same-day review pass below; see decision 11.)

**Tests** — `application-service/api/tests/test_h4_resource_naming.py` (new): two
infra ids sharing the old `[:8]` prefix get distinct target-group and log-group names;
the discriminator survives slug truncation at the 32-char ALB limit; a foreign/adopted
target group (tag mismatch) raises on both the `DuplicateTargetGroupNameException` path
and the plain-success/matching-settings path (a hand-rolled fake models the latter,
since `api/mock/mock_session.py`'s shared mock only reproduces the former); a
same-app/same-tags re-adoption still succeeds (self-healing); an app with a stored
target-group ARN never calls `create_target_group` at all; a brand-new app's first
deploy persists the hashed log group and passes it to `ECSClient.create_task_definition`;
a pre-H4 app (a `Deployment` row with `status=SUCCEEDED` already exists, no stored name)
keeps the legacy log group even across an `ApplicationRetryDeployView`-style
`task_definition_arn` reset; a row with an already-stored name reuses it unchanged.
`test_runtime_logs.py` gained one test proving the runtime-logs tail reads
`Application.log_group_name` when present. `test_cost_tagging.py`'s `_FakeALBClient`
gained a `describe_tags` so it still models a real elbv2 client now that
`create_target_group` always checks tags when given any. Final state: 454
application-service tests pass (433 pre-existing + 21 new/modified across this file and
the two edits above), 880 infrastructure-service tests pass unchanged (H4 touched no
infra-service test), 74 gateway-service tests pass unchanged (H4 touched no
gateway-service code or test — the count includes H5's unrelated additions, merged in by
this branch's rebase onto `origin/main`).

**REAL-AWS-VALIDATION:** a new `## H4 truncated-UUID resource names` section was added
to `plan/REAL-AWS-VALIDATION.md` — not yet run against a real account. Summary: which
AWS response shape `CreateTargetGroup` actually returns for matching vs. differing
settings on an existing name (this decides which branch the ownership check fires on),
`DescribeTags` read-after-write consistency right after `CreateTargetGroup(Tags=...)`,
and that a brand-new app's first real deploy lands on the hashed log group while a
pre-H4 app's redeploy keeps its existing one.

**Independent security review — fixes applied (second pass, same branch):**

9. **R1 (REQUIRED): pre-F3 apps with no Deployment history.** Migration 0029 created
   the `Deployment` table with no backfill, and `_record_deployment` is best-effort (a
   history row failing to write never fails the deploy it describes) — so an app
   deployed before that table existed, or whose own history row silently failed to
   write, can have neither a `Deployment` row nor `log_group_name`. Decision 5's
   `_has_succeeded_before` originally checked Deployment history only, which would have
   read such a row as "never deployed" on its very next deploy and minted a brand-new
   hashed log group, orphaning its actual runtime log history. Fixed three ways,
   deliberately redundant, in priority order: (a) `_has_succeeded_before` checks
   `deployment_url` first — set only by a fully successful deploy
   (`deploy_application`'s step 9) and never reset by `ApplicationRetryDeployView`
   (that view resets `status`/`error_message`/`service_arn`/`task_definition_arn`/
   `target_group_arn`/`listener_rule_arn`/`runtime_refs`, not this field and not
   `build_id`); (b) `task_definition_arn`/`service_arn` next, covering an app whose
   first deploy attempt got that far but hasn't gone through a retry-reset yet; (c)
   Deployment history last, as the final backstop. A new migration,
   `0035_backfill_legacy_log_group_name.py`, persists `log_group_name =
   f"/ecs/{slug}-task"` for every existing `Application` matching any of those same
   three signals — the slug regex is duplicated inline in the migration rather than
   imported from `api.common.naming`, since a migration must not depend on application
   code that can change independently of its own, fixed historical meaning.
   - **Why `deployment_url` and not just (b)+(c):** an earlier version of this decision
     claimed persisting `log_group_name` via migration made `_has_succeeded_before`'s
     exact signal not matter once the migration had run — that was wrong. A retry
     queued in the window between `ApplicationRetryDeployView` resetting the ARN
     fields and a worker actually picking up that retry's job (migration timing is
     irrelevant here — this is a race between the reset and the *worker*, not the
     migration) would leave a pre-F3, zero-Deployment-row app with neither ARN field
     nor a `Deployment` row, and `_create_task_definition` would still mint a hashed
     name in that exact instant. `deployment_url`, set at the app's original success
     and untouched by the retry view, closes this without depending on timing at all —
     caught and fixed in a second review pass on this same decision.
   - Tests:
     `test_pre_f3_app_with_task_definition_arn_but_no_deployment_history_keeps_legacy_log_group`
     (no `Deployment` rows, `task_definition_arn` set → legacy name),
     `test_retry_queued_before_migration_runs_still_keeps_legacy_log_group_via_deployment_url`
     (neither ARN field nor any `Deployment` row, only `deployment_url` → legacy name —
     the exact race above), and `test_migration_0035_backfills_only_rows_with_deploy_evidence`
     (calls the migration's `RunPython` function directly against real models — this
     codebase's test `conftest.py` builds the schema from model state and never
     actually runs migrations, so this is the only way to exercise it under the
     existing test setup; it verifies the migration's logic, not its compatibility
     with a future model schema change, since it's handed live models rather than
     migration-historical ones).
   - **Migration numbering:** `feat/h1-envs-encryption` also adds an
     `application-service` migration `0034` (`0034_encrypt_application_envs.py`,
     unrelated name), off the same `0033_custom_domain_route` parent. Neither branch was
     merged into `main` as of this review, so this branch's own `0034`/`0035` were left
     as-is; whichever of H1/H4 merges second renumbers its migration(s) and repoints
     `dependencies` at rebase time.
10. **C1: `launchpad:app-id` on new target groups.** `app_tags()`'s two keys
    (`launchpad:infra`, `launchpad:app`) are both name-shaped — a delete-then-recreate
    of an app under the same name on the same infra would tag its new target group
    identically to its predecessor's on that axis alone. `target_group_name()`'s
    per-row hash already makes the two land on different names in practice, so this
    was defense-in-depth, not a live gap: `aws/tags.py:target_group_tags()` adds
    `launchpad:app-id` (the full `Application.id`) on top of `app_tags()`, and
    `_create_target_group` passes it as the ownership check's expected tags. Existing
    target groups (reused via the stored-ARN path) are unaffected and not re-tagged —
    that path still doesn't call the ownership check at all, unchanged from before this
    review; re-verifying a *stored* ARN's ownership is a larger, separate change than
    this item's scope.
11. **C2: deterministic wrong-VPC fallback name.** The old
    `f"{name[:24]}-{int(time.time()) % 10000}"` truncated `name`'s own hash
    discriminator to 3 characters and derived the replacement from wall-clock time.
    `ALBClient._vpc_discriminated_name` hashes the identifying tags
    (`launchpad:infra`/`launchpad:app-id`, falling back to `name` itself when no tags
    were given) together with `vpc_id` — the actual differing input — so the same
    inputs always produce the same output, and reserves its own `-{hash}-tg` suffix
    the same way `target_group_name` does. The misleading "residual" comment from
    decision 8 (this fallback "still truncates its hash to 3 chars") is now stale by
    construction and was rewritten in `aws/alb.py` at the call site.
12. **C3: retry `DescribeTags` before failing.** `_verify_target_group_ownership` now
    retries up to twice (3 attempts total) with a short backoff (0.1s, 0.2s) before
    raising `TargetGroupOwnershipMismatch`, in case tag propagation lags
    `CreateTargetGroup(Tags=...)` by a moment on a target group this same call just
    created. Whether real AWS actually has such a lag is unconfirmed — added to
    `plan/REAL-AWS-VALIDATION.md`.
13. **C4: actionable, disclosure-safe mismatch message.** `TargetGroupOwnershipMismatch`
    now names the target group and its ARN and tells the customer what to do ("delete
    that target group yourself, or rename this application... then redeploy"). This
    reaches `Application.error_message` verbatim (`sanitize_deploy_error` passes a
    plain `RuntimeError`'s `str()` through unchanged) — safe to show in full because
    it's the customer's own AWS account and their own target group being named back to
    them.

Tests added across this review round (two passes — R1's `deployment_url` fix landed in
a follow-up to the same review): 11 new (31 total in `test_h4_resource_naming.py`, up
from 20), covering R1 (including the retry/migration-timing race), C1, C2
(determinism + length/charset), and C3 (retry-then-succeed, retry-then-exhaust)
directly, plus C4 (message content, survives `sanitize_deploy_error`).
`test_cost_tagging.py`'s `test_create_target_group_call_site_passes_app_tags` now
asserts `app_tags()`'s two keys are a *subset* of what's passed, not the whole dict,
since target groups now carry a third key. Final state: 465 application-service tests
pass, 880 infrastructure-service tests and 84 gateway-service tests pass unchanged
(gateway's count reflects H3's own additions from the rebase onto the now-updated
`origin/main`, not anything H4 touched). `ruff`, `compileall`,
`iam_policy/generate.py --check`, and `makemigrations api --check --dry-run` (against
Postgres connection settings with no live Postgres — "No changes detected" is reported
before the connection is attempted) all clean.

## H5 — Gateway path ids typed as UUID

**Status: done** (`fix/h5-gateway-uuid-params`).

Only routes added in F0–F6/F1b take `uuid.UUID`; 59 older path params are `str`, which lets
`%3F`/`%23` change the upstream URL (confused deputy — upstream authz still applies).
Type them all; a malformed id returns 422 at the edge.

**Decisions**

- Every id-shaped path param across `auth.py`, `infrastructure.py`, `database.py`,
  `application.py` (including the `/webhooks/github/{app_id}` route) is now `uuid.UUID`,
  verified against the owning model: `Infrastructure.id`, `Application.id`,
  `Deployment.id`, `Database.id` (all Django `UUIDField`s, confirmed via
  `deployment-services/*/api/models/*.py` and `<uuid:...>` URL converters in
  `application-service/api/urls.py`), and `InvitedUser.id` in auth-service
  (`identity-services/services/auth-service/src/db/models/invited-user.model.ts`,
  `DataTypes.UUID`). `custom_domain.py` was already fully typed and untouched.
- One path param stays `str`, deliberately not `uuid.UUID`, because the id it names is
  **not** a UUID-typed column upstream: `user.py`'s `/users/{user_id}` (user-service
  `User.user_id` is `DataTypes.STRING`,
  `identity-services/services/user-service/src/db/models/user.model.ts`). In practice
  it's always populated with auth-service's UUID at event time, but the schema doesn't
  guarantee that, so hard-typing `uuid.UUID` would be a claim the model doesn't back. It
  instead takes `Path(pattern=r"^[A-Za-z0-9_-]{1,128}$")`, which forbids `/ ? # %` and
  dot-segments while staying open to any future non-UUID id shape.
  **Updated by H3:** `notification.py`'s `/notifications/user/{user_id}` — the other
  non-UUID id at the time this was written — no longer exists. H3 replaced it with
  `GET /notifications/me`, which derives the id from the caller's verified token instead
  of a path param, so there's nothing left there to type or pattern-constrain.
- `proxy_request` itself wasn't changed: every path segment it now receives is either a
  `uuid.UUID` (whose `str()` form is fixed and safe) or a `Path(pattern=...)`-validated
  string, so the URLs endpoints hand it are safe by construction. A generic quoting helper
  would be redundant given that invariant, and the new regression test
  (`test_path_param_ids_are_constrained.py::test_every_path_param_is_uuid_or_pattern_constrained`)
  enforces the invariant for any future route.
- No frontend changes needed — every id the frontend sends is passed through as an opaque
  string taken from a prior API response's `id` field, and none of the newly-UUID-typed
  routes are called with anything else (confirmed by reading `launchpad-frontend/lib/api/*`
  and its call sites). The `str`-typed `/api/users/{user_id}` and
  `/api/webhooks/github/{app_id}` aren't called by the frontend at all (H3 later gave
  `/api/notifications/user/{user_id}` a frontend-visible replacement — see H3).

## H6 — Small items

**Status:** done (mock-verified; item 1's ALB behavior is unverified against a real
controller — see plan/REAL-AWS-VALIDATION.md's H6 entry — item 5 was found to be
out of scope and left as-is, see Decision 5).

- EKS: unmatched `:443` traffic gets 503 (empty-backend Service) rather than a fixed 404.
- Custom domains: an operator path to force-disable a domain (abuse/takedown).
- Databases: a tighter per-user write bucket for create/delete (F0 follow-up).
- Consumers that retry inside a pika callback with a bare `time.sleep()` (H2's note: this
  starves the connection's heartbeat) — swept across both services.
- dns_writer Redis ACL + RabbitMQ user wired into `infra/.docker` for local dev (documented
  in `docs/PLATFORM_DNS_ISOLATION.md`, not automated).

### Files

- `infrastructure-service/api/services/eks_bootstrap.py` (`_ensure_bootstrap_ingress`),
  `api/tests/test_eks_bootstrap.py`.
- `infrastructure-service/api/management/commands/force_disable_custom_domain.py` (new),
  `api/tests/test_force_disable_custom_domain.py` (new).
- `infrastructure-service/api/views/database.py`, `core/settings.py`, `test_settings.py`,
  `env.example`, `shared/ratelimit/budget.py` (`rate_limited`'s new `methods` filter),
  `api/tests/test_database_api.py`.
- `application-service/api/messaging/consumers/{infrastructure,environment,user}.py`,
  `infrastructure-service/api/messaging/consumer/application_consumer.py`,
  `api/tests/test_h6_consumer_heartbeat_sleep.py` (new, application-service),
  `api/tests/test_h6_application_consumer_sleep.py` (new, infrastructure-service),
  `api/tests/test_host_readiness_consumer.py` / `test_infra_exited_consumer.py` (updated —
  their `time.sleep` monkeypatches no longer apply since the module stopped importing
  `time`).

### Decisions

1. **EKS default action: `fixed-response` + `use-annotation`, no backing Service at all.**
   The old default backend was a real `ClusterIP` Service with a selector matching no pods
   — unmatched traffic hit a target group with zero healthy targets, and the ALB answered
   with a bare 503 indistinguishable from a real outage. The AWS Load Balancer Controller's
   `alb.ingress.kubernetes.io/actions.<name>` annotation resolves the backend by name before
   ever doing a Service/Endpoints lookup, so the fix needs no Service object at all —
   `_ensure_bootstrap_ingress` no longer creates one. `_get_or_create` swallows a 409 on
   `Ingress/bootstrap` and never updates an existing object (same as `_ensure_ingress_class`,
   see `_ingress_group_name`'s docstring on that pattern) — a cluster that already
   bootstrapped before this change keeps its old Ingress spec (and its now-orphaned
   `default-backend` Service, no longer referenced by anything) and keeps returning 503
   forever; only a cluster bootstrapping for the first time after this change gets the
   fixed-404 default. Unverified against a real controller (added to
   plan/REAL-AWS-VALIDATION.md, and that check must run against a fresh cluster, not an
   already-bootstrapped one): whether EKS Auto Mode's specific ALB controller build honors
   `use-annotation` the way AWS's own documentation describes.
2. **`force_disable_custom_domain` reuses `CustomDomainService._teardown` directly**, the
   same private helper `delete_domain`/`disable_for_application`/every periodic sweep
   already call — an operator-forced disable tears a domain down through the identical
   DISABLING -> detach -> certificate-delete -> DISABLED path, not a second, unaudited one.
   `--confirm` is required to mutate anything; without it the command only reports the
   target (status, infra) and exits — the same "dry-run by default" shape
   `republish_exited_events --dry-run` uses, inverted (here the safe default has no flag).
   Lookup is by `--domain-id` (exact) or `--hostname` (normalized the same way a claim
   normalizes it, excluding already-DISABLED rows, refused as ambiguous if more than one
   non-disabled row matches — forcing `--domain-id`). The audit line is a structured
   `audit.custom_domain` logger record only, no new DB table — this is a low-volume
   operator action, not a per-request access log like `exit_export_audit.py`'s.
3. **`databases_write` is a second, stacked `@rate_limited` decorator, not a replacement
   for `databases`.** `shared.ratelimit.budget.rate_limited` gained an optional `methods`
   filter so the tighter budget only charges POST (create) / DELETE — a GET under the same
   view function passes through uncharged. This keeps `databases` as the general read+write
   ceiling (F0) and layers a much tighter one (10/300s default) specifically on the two
   operations that provision or tear down a real AWS resource, matching `evidence`/
   `exit_export`'s existing pattern of a tighter budget for AWS-touching endpoints.
4. **Every bare `time.sleep()` inside a pika callback's retry path is now
   `ch.connection.sleep()`**, matching the fix H2 already applied to
   `InfraExitedEventConsumer` (see that section's docstring for the full "starves the
   BlockingConnection's heartbeat" reasoning) — `InfraEventConsumer`,
   `InfraUpdatedEventConsumer`, `HostReadinessEventConsumer`,
   `EnvironmentEventConsumer` (both its retry branches), `AuthEventConsumer`, and
   infrastructure-service's `ApplicationEventConsumer`. `import time` was removed from
   every file where it became unused. No behavior change to retry counts, backoff, or ack
   semantics — only what the process does while waiting.
5. **dns_writer Redis ACL + RabbitMQ user: left undone, and left documented as such.**
   `docs/PLATFORM_DNS_ISOLATION.md` already explains why in detail: the compose stack's
   Redis runs a single `--requirepass` line that can't add an ACL user without an
   `aclfile`, which would change how every other service in the stack authenticates to
   Redis, and RabbitMQ's single shared `guest` user would need a dedicated vhost/user
   threaded through every other service's `RABBITMQ_URL` to add per-user permissions. The
   item's own acceptance criterion — "only if it doesn't change how other services
   authenticate" — is not met by any change available here, so no code or compose file
   changed for this item; the existing documentation already carries this as a tracked
   follow-up.

## H7 — Residuals

**Status: done** (mock-verified; item 5's core SQL statement and item 7's Redis half
additionally verified against real `postgres:16-alpine`/`redis:7` containers — see
below).

Closes the remaining low-priority residuals called out across H3's and H6's own review
passes (decision 5 above in particular — H7 item 7 revisits that "not achievable" call
with a narrower mechanism, an ACL file / management-API approach neither H3 nor H6 tried).

1. **`JWTAuthMiddleware` 500 on any non-`HttpError` JWT failure.** `shared/middleware/
   authentication.py` caught `HttpError` then fell through to a bare `except Exception`,
   turning an expired/malformed token or a `ScopedTokenRejected` (H3) into a 500 with the
   exception text in `details`. Now catches `jwt.PyJWTError` (the common base of
   `ScopedTokenRejected` and every PyJWT decode failure) between the two and returns a
   fixed 401 body (`{"message": "Invalid or expired token", "details": null}`), never the
   exception's own text. `payment-service`'s `JWTAuthentication` (DRF `BaseAuthentication`)
   does **not** have the same shape — it already catches `Exception` broadly around
   `decode_jwt` and returns `None` (unauthenticated) rather than 500ing, so it was left
   unchanged. Tests: `infrastructure-service/api/tests/test_shared_jwt_middleware_401.py`
   (expired, malformed, and scoped tokens all 401 with the fixed body; a valid token still
   reaches the view).
2. **`.gitignore`'s `*.log*` substring trap.** Flagged in H3's residuals: the rule matched
   any path *containing* `.log`, not just log files, and had already caught a real source
   file once. Replaced with `*.log.[0-9]*` (rotated logs) alongside the pre-existing
   `*.log`/`logs/` rules; no rotation config anywhere in the repo produces any other
   suffix. `git status --ignored` before/after is identical except for `.gitignore`
   itself — nothing tracked or untracked changed ignore status.
3. **OTP verify timing leaked account existence.** `authenticateWithOTP`/`verifyResetOTP`
   used to look the account up first and only run the OTP-row query (and open a
   transaction) for a *known* email — an unknown email short-circuited before ever
   touching `UserOTP`. Folded into item 5's redesign: both flows now run the exact same
   two DB operations (one atomic claim, one `UserOTP.findOne`) regardless of whether the
   email is registered, the second bound to a nil UUID (`NIL_INVITED_USER_ID`) when it
   isn't. Test: `otp-attempt-cap.test.ts`'s "run identical DB operations" case asserts the
   literal call sequence (`claimAttempt` → `transaction` → `UserOTP.findOne`) is the same
   list for both an unknown email and a known email with a wrong guess.
4. **Multi-infra invites: `createOTP` dedup invalidated another infra's live code.**
   `invited-user.base.service.ts`'s `createOTP` deleted every prior same-purpose OTP
   before minting a new one — a user invited to infra A, then invited to infra B before
   verifying A's still-valid code, had A's code silently deleted as a side effect of B's
   invite. Register-purpose dedup is now scoped to `(user, purpose, infra_id)`;
   password-reset stays `(user, purpose)` only, since a reset is one per-account flow, not
   per-infra. The attempt-cap budget (item 5) stays shared across every live code for the
   account either way — this widens which code can be *outstanding*, never how many
   guesses an attacker gets. Tests: `inviter-user.crud.service.register-otp.test.ts` gained
   a case inviting an existing, unverified user to a second infra and asserting the dedup
   delete is scoped to that infra.
   **User-visible consequence, intended:** a user with codes outstanding for both infra A
   and infra B who verifies B still has A's row live — `login()`'s `pendingOtp` check
   (scoped to the register purpose, unaffected by this item) blocks that user's normal
   password login until A's code expires (≤10 min) or is itself verified/dedup'd out by a
   third invite to A. Pre-H7, verifying B would have deleted A's code and cleared the
   block immediately; this is the direct trade-off for A no longer being silently
   invalidated by B's invite in the first place.
5. **OTP attempt cap failed closed on a Redis outage.** `otp-attempts.ts`'s `claimAttempt`/
   `clearAttempts` moved off Redis onto `InvitedUser` (`failed_otp_attempts` +
   `otp_attempts_window_start`, added idempotently in `db/index.ts` next to the existing
   `purpose` `ALTER`, plus on the Sequelize model — auth-service has no migration
   framework). `claimAttempt(email)` is one atomic `UPDATE ... RETURNING` keyed on
   `email`: its `WHERE` clause is the only place that decides whether the account exists,
   and its `SET` clause claims the slot, so a zero-row result (unknown email) both counts
   as "not allowed to have counted anything" *and* runs the identical query/round-trip a
   known email's claim does (closing item 3 as a side effect). Semantics unchanged from
   the Redis version: claimed before the guess is evaluated, cap `MAX_OTP_ATTEMPTS` (5),
   invalidation on exceed and the reset-to-0 on success both outside/inside the right
   transaction boundary as before, 10-minute window. `shouldThrottleForgotPassword` stays
   on Redis, still fails open, unchanged. Tests: `otp-attempts.test.ts` (claim/reset
   against a mocked `sequelize.query`, per-account isolation) and
   `otp-attempt-cap.test.ts`'s concurrency case (6 concurrent `authenticateWithOTP` calls
   against a shared in-memory row via a mocked `sequelize.query`, asserting exactly 5
   reach the OTP lookup and the 6th is a 429 — the real `claimAttempt` code path, not a
   mock of it). The `UPDATE ... RETURNING` statement itself was additionally run
   against a real `postgres:16-alpine` container (a table with the two new columns, one
   row): six calls with a literal email returned `failed_otp_attempts` 1..6 with a
   stable window timestamp; manually rewinding `otp_attempts_window_start` past 10
   minutes and re-running reset the count to 1; the same statement against an email with
   no matching row returned zero rows and left the real row untouched.
6. **`ECSClient.create_task_definition`'s `family` shared across infras with the same
   app slug.** Same class of collision H4 already fixed for target groups and log
   groups, left as a deliberate residual there. `Application.task_family` (additive
   migrations `0038`/`0039`, the latter backfilling the legacy `{slug}-task` name for
   every app with deploy evidence, mirroring `0034`/`0035` exactly) now carries a
   per-infra+per-app hashed family (`api/common/naming.py:new_ecs_task_family`/
   `ecs_task_family_for`) for new deploys, with the same `_has_succeeded_before`
   three-signal legacy fallback log_group_name uses. Every place that derived the family
   inline now reads the stored value: `_create_task_definition` (deploy/rollback/backfill
   all funnel through this one method), `_create_ecs_service`'s `container_name`
   (must match the container's actual name inside the task definition),
   `runtime_logs_service.py`'s ECS log-stream binding, and `exit_inventory.py`'s rendered
   task definition. Tests: `application-service/api/tests/test_h7_task_family_naming.py`
   (new/legacy/stored family selection, the `_create_ecs_service` call site, exit
   inventory and its legacy fallback, migration `0039`'s backfill selectivity).
7. **dns_writer Redis ACL + RabbitMQ user, wired into `infra/.docker`, opt-in.** H6
   decision 5 called this not achievable without changing how every other service
   authenticates; revisited with a narrower mechanism than a shared `--requirepass` line
   or a `definitions.json` import. Redis: `infra/.docker/init/redis-entrypoint.sh` renders
   an `aclfile` only when `DNS_WRITER_REDIS_PASSWORD` is set, keeping `default` on the
   same `REDIS_PASSWORD` every other service already uses and adding a
   `launchpad_dns_writer` user scoped to `~platform_dns:*` — verified against a real
   `redis:7` container (`docker run ... --aclfile`): default still authenticates with
   `REDIS_PASSWORD`, `launchpad_dns_writer` gets `NOPERM` on `infra:*` and `OK` on
   `platform_dns:*`. Left unset, the entrypoint runs the original `--requirepass` line
   verbatim. RabbitMQ: a one-shot `rabbitmq-init` service
   (`init/rabbitmq-dns-writer-init.sh`) calls the management HTTP API to `PUT` a
   `launchpad_dns_writer` user and the same configure/write/read permission patterns
   `docs/PLATFORM_DNS_ISOLATION.md` already documented, only when
   `DNS_WRITER_RABBITMQ_PASSWORD` is set — never a `definitions.json` import, which could
   redefine `RABBITMQ_DEFAULT_USER`'s own permissions/existence on load. `docker compose
   config` diffed against the pre-H7 render confirms only the `redis` service and the new
   `rabbitmq-init` service changed — every other service's `REDIS_URL`/`RABBITMQ_URL` line
   is byte-identical. `docs/PLATFORM_DNS_ISOLATION.md` updated in place; not verified
   end-to-end against a real `rabbitmq:4-management` container (no image pull available in
   this environment) — the management API calls themselves are standard, idempotent PUTs.

### Files

- `deployment-services/shared/middleware/authentication.py`,
  `deployment-services/infrastructure-service/api/tests/test_shared_jwt_middleware_401.py`
  (new).
- `.gitignore`.
- `identity-services/services/auth-service/src/utils/otp-attempts.ts` (rewritten),
  `src/db/index.ts`, `src/db/models/invited-user.model.ts`,
  `src/service/invited-users/invited-user.base.service.ts`,
  `src/service/invited-users/invited-user.auth.service.ts`,
  `src/service/invited-users/invited-user.password.crud.service.ts`,
  `src/utils/otp-attempts.test.ts` (rewritten),
  `src/service/invited-users/otp-attempt-cap.test.ts` (rewritten),
  `src/service/invited-users/inviter-user.crud.service.register-otp.test.ts`.
- `application-service/api/models/application.py`, `api/common/naming.py`,
  `api/services/application_deployment_service.py`, `api/services/exit_inventory.py`,
  `api/services/runtime_logs_service.py`, `api/migrations/0038_application_task_family.py`
  (new), `api/migrations/0039_backfill_legacy_task_family.py` (new),
  `api/tests/test_h7_task_family_naming.py` (new).
- `infra/.docker/docker-compose.yml`, `infra/.docker/init/redis-entrypoint.sh` (new),
  `infra/.docker/init/rabbitmq-dns-writer-init.sh` (new), `infra/.docker/env.example`,
  `docs/PLATFORM_DNS_ISOLATION.md`.

### Tests

`application-service`: 571 passed, including the 11 new cases in
`test_h7_task_family_naming.py`. `infrastructure-service`: full suite green, including
the new middleware test file.
`gateway-service`: 84 passed, unchanged (H7 touched no gateway code). `auth-service`: 61
`node --test` cases pass across the full `src/**/*.test.ts` glob, including the rewritten
OTP attempt-cap and dedup suites. `ruff check deployment-services gateway-service
payment-service`, `compileall`, `iam_policy/generate.py --check`, and
`makemigrations api --check --dry-run` (application-service, `MODE=dev`, no live
Postgres — "No changes detected") all clean. `pnpm format`/`pnpm lint`/
`tsc --noEmit` clean across `identity-services`.
