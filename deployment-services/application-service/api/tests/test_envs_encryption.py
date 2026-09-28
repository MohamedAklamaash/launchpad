"""H1: Application.envs is Fernet ciphertext at rest (api/fields.py).

Covers: round-trip through the ORM, ciphertext actually on disk (raw SQL, no plaintext),
key rotation (old key still decrypts, rotate re-encrypts, removing the old key afterwards
still reads), the backfill migration against rows seeded as pre-migration plaintext, startup
failure with no key outside dev, the F3 snapshot hash staying stable across encryption, and
no plaintext reaching logs on a real deploy call.

Also covers the security-review follow-ups (R1-R3, RECOMMENDED 1-4): repairing rows
corrupted by an old/new code overlap, lost-update-safe rotation, refusing known-weak keys,
`.defer('envs')` on paths that don't need it plus the startup decryption canary, the PATCH
view's error handling, and ValidationError from `to_python`.
"""
import importlib
import json
import logging
import uuid

import pytest
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import override_settings

from api.common.envs_encryption import DEV_FALLBACK_KEY, load_keys
from api.fields import (
    EncryptedJSONField,
    EnvsDecryptionError,
    EnvsJSONQuotedTokenError,
    EnvsNotMigratedError,
    decrypt_value,
)
from api.services.deployment_snapshot import snapshot_env

SECRET_VALUE = "sk-live-H1-SEEDED-SECRET-" + uuid.uuid4().hex
KEY_OLD = Fernet.generate_key().decode()
KEY_NEW = Fernet.generate_key().decode()


@pytest.fixture
def app(schema_db):
    from api.models.application import Application
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@x.com", user_name="t")
    infra = Infrastructure.objects.create(
        user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws", max_cpu=1024, max_memory=2048,
    )
    return Application.objects.create(
        user=user, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/o/r", project_branch="main",
        project_commit_hash="a" * 40, envs={"NODE_ENV": "production", "API_KEY": SECRET_VALUE},
        port=8080, alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
    )


def _raw_envs_column(app_id) -> str:
    from api.models.application import Application

    # UUIDField's DB representation is backend-specific (hex, no dashes, on SQLite;
    # native uuid on Postgres) — go through the field's own get_db_prep_value rather than
    # str(app_id), which only matches on backends that happen to store the dashed form.
    prepped = Application._meta.get_field("id").get_db_prep_value(app_id, connection)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT envs FROM {_table()} WHERE id = %s", [prepped])
        return cursor.fetchone()[0]


def _table() -> str:
    from api.models.application import Application
    return Application._meta.db_table


def _prepped_id(app_id):
    from api.models.application import Application
    return Application._meta.get_field("id").get_db_prep_value(app_id, connection)


# ── round trip + ciphertext at rest ─────────────────────────────────────────────

@pytest.mark.django_db
def test_round_trip_through_the_orm(app):
    app.refresh_from_db()
    assert app.envs == {"NODE_ENV": "production", "API_KEY": SECRET_VALUE}


@pytest.mark.django_db
def test_ciphertext_on_disk_never_the_plaintext_value(app):
    raw = _raw_envs_column(app.id)
    assert SECRET_VALUE not in raw
    assert "API_KEY" not in raw  # key names are also inside the encrypted JSON blob
    # It really is Fernet ciphertext, not e.g. base64'd JSON that happens to hide the value.
    assert decrypt_value(raw) == {"NODE_ENV": "production", "API_KEY": SECRET_VALUE}


@pytest.mark.django_db
def test_empty_and_null_envs_round_trip(schema_db):
    from api.models.application import Application
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@x.com", user_name="t")
    infra = Infrastructure.objects.create(
        user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws", max_cpu=1024, max_memory=2048,
    )
    empty = Application.objects.create(
        user=user, infrastructure=infra, name="empty-app", project_remote_url="https://github.com/o/r",
        project_branch="main", project_commit_hash="a" * 40, envs={}, port=8080,
        alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
    )
    null = Application.objects.create(
        user=user, infrastructure=infra, name="null-app", project_remote_url="https://github.com/o/r",
        project_branch="main", project_commit_hash="a" * 40, envs=None, port=8080,
        alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
    )
    empty.refresh_from_db()
    null.refresh_from_db()
    assert empty.envs == {}
    assert null.envs is None
    assert _raw_envs_column(null.id) is None


# ── rotation ─────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_rotation_old_key_decrypts_rotate_reencrypts_then_old_key_removed(app):
    from io import StringIO

    from django.core.management import call_command

    with override_settings(APP_ENVS_ENCRYPTION_KEYS=(KEY_OLD,)):
        app.envs = {"API_KEY": SECRET_VALUE}
        app.save(update_fields=["envs"])

    raw_before = _raw_envs_column(app.id)
    assert Fernet(KEY_OLD).decrypt(raw_before.encode("ascii"))  # written under the old key
    with pytest.raises(InvalidToken):
        Fernet(KEY_NEW).decrypt(raw_before.encode("ascii"))  # not yet under the new one

    with override_settings(APP_ENVS_ENCRYPTION_KEYS=(KEY_NEW, KEY_OLD)):
        out = StringIO()
        call_command("rotate_envs_encryption_key", stdout=out)
        assert "rotated=1" in out.getvalue()

        raw_after = _raw_envs_column(app.id)
        assert Fernet(KEY_NEW).decrypt(raw_after.encode("ascii"))  # now under the newest key

        # Re-running is a no-op: the row is already current.
        out2 = StringIO()
        call_command("rotate_envs_encryption_key", stdout=out2)
        assert "rotated=0" in out2.getvalue()
        assert "already_current=1" in out2.getvalue()

    # The old key can now be dropped entirely and the row still reads.
    with override_settings(APP_ENVS_ENCRYPTION_KEYS=(KEY_NEW,)):
        app.refresh_from_db()
        assert app.envs == {"API_KEY": SECRET_VALUE}


@pytest.mark.django_db
def test_rotate_dry_run_reports_but_does_not_write(app):
    from io import StringIO

    from django.core.management import call_command

    with override_settings(APP_ENVS_ENCRYPTION_KEYS=(KEY_OLD,)):
        app.envs = {"API_KEY": SECRET_VALUE}
        app.save(update_fields=["envs"])
    raw_before = _raw_envs_column(app.id)

    with override_settings(APP_ENVS_ENCRYPTION_KEYS=(KEY_NEW, KEY_OLD)):
        out = StringIO()
        call_command("rotate_envs_encryption_key", "--dry-run", stdout=out)
        assert "[dry-run]" in out.getvalue()
        assert "rotated=1" in out.getvalue()

    assert _raw_envs_column(app.id) == raw_before


# ── data migration on pre-existing plaintext rows ───────────────────────────────

@pytest.mark.django_db
def test_migration_encrypts_existing_plaintext_rows(app):
    import importlib
    import json

    from django.apps import apps as django_apps

    migration = importlib.import_module("api.migrations.0037_encrypt_application_envs")
    encrypt_existing_envs = migration.encrypt_existing_envs
    decrypt_existing_envs = migration.decrypt_existing_envs

    plaintext = json.dumps({"API_KEY": SECRET_VALUE, "NODE_ENV": "staging"})
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {_table()} SET envs = %s WHERE id = %s", [plaintext, _prepped_id(app.id)])
    assert SECRET_VALUE in _raw_envs_column(app.id)  # sanity: seeded as plaintext, pre-migration

    schema_editor = type("SchemaEditor", (), {"connection": connection})()
    encrypt_existing_envs(django_apps, schema_editor)

    raw = _raw_envs_column(app.id)
    assert SECRET_VALUE not in raw
    app.refresh_from_db()
    assert app.envs == {"API_KEY": SECRET_VALUE, "NODE_ENV": "staging"}

    decrypt_existing_envs(django_apps, schema_editor)
    raw_reversed = _raw_envs_column(app.id)
    assert json.loads(raw_reversed) == {"API_KEY": SECRET_VALUE, "NODE_ENV": "staging"}


@pytest.mark.django_db
def test_post_migration_plaintext_in_column_is_an_error(app):
    """After the migration, anything in the column that isn't ciphertext under a
    configured key must fail loudly on read, never be silently accepted as plaintext.
    Raised as EnvsNotMigratedError specifically: a plain-JSON value can't be told apart
    from "not migrated yet" by its shape alone, and the canary in api/apps.py relies on
    that specific subclass to know this isn't a key problem."""
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {_table()} SET envs = %s WHERE id = %s", ['{"A": "b"}', _prepped_id(app.id)])

    with pytest.raises(EnvsNotMigratedError):
        app.refresh_from_db()


# ── startup failure with no key outside dev ─────────────────────────────────────

def test_load_keys_requires_a_key_outside_dev_mode():
    with pytest.raises(ValueError, match="APP_ENVS_ENCRYPTION_KEYS must be set"):
        load_keys("", is_dev=False)


def test_load_keys_falls_back_to_the_fixed_dev_key(caplog):
    with caplog.at_level(logging.WARNING):
        keys = load_keys("", is_dev=True)
    assert keys == (DEV_FALLBACK_KEY,)
    assert "NOT confidential" in caplog.text


def test_load_keys_rejects_a_malformed_key():
    with pytest.raises(ValueError, match="invalid Fernet key"):
        load_keys("not-a-real-fernet-key", is_dev=False)


def test_load_keys_parses_comma_separated_newest_first():
    keys = load_keys(f"{KEY_NEW}, {KEY_OLD}", is_dev=False)
    assert keys == (KEY_NEW, KEY_OLD)


# ── F3 snapshot hash stays stable across encryption ─────────────────────────────

@pytest.mark.django_db
def test_snapshot_hash_is_unchanged_by_the_encrypt_decrypt_round_trip(app):
    """snapshot_env hashes whatever `application.envs` returns — a plain dict either way,
    since the field decrypts before Python code ever sees it — so H7's content hash must
    be identical before and after a DB round trip."""
    plain = {"NODE_ENV": "production", "API_KEY": SECRET_VALUE}
    _, hash_from_the_dict_directly = snapshot_env(app.id, plain)

    app.refresh_from_db()
    _, hash_after_round_trip = snapshot_env(app.id, app.envs)

    assert hash_from_the_dict_directly == hash_after_round_trip
    assert SECRET_VALUE not in hash_after_round_trip


# ── Django admin must not corrupt envs into a string ────────────────────────────

@pytest.mark.django_db
def test_admin_form_round_trips_a_dict_not_a_string(app):
    """Application is registered in Django admin (api/admin.py) with no custom form. A
    bare TextField would hand admin a CharField/Textarea: editing and saving would wrap
    whatever text came back in a JSON string, turning envs into a str for every later
    reader. EncryptedJSONField.formfield() must make admin behave like it would for a
    plain JSONField instead."""
    from django import forms
    from django.forms import modelform_factory

    from api.models.application import Application

    Form = modelform_factory(Application, fields=["envs"])
    field = Form.base_fields["envs"]
    assert isinstance(field, forms.JSONField)

    form = Form(data={"envs": '{"NODE_ENV": "staging"}'}, instance=app)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["envs"] == {"NODE_ENV": "staging"}

    saved = form.save()
    assert isinstance(saved.envs, dict)
    saved.refresh_from_db()
    assert saved.envs == {"NODE_ENV": "staging"}


# ── no plaintext reaches logs on a real deploy call ─────────────────────────────

@pytest.mark.django_db
def test_no_plaintext_env_value_in_logs_during_task_definition_creation(caplog):
    from unittest.mock import MagicMock, patch

    from aws.ecs import ECSClient as RealECSClient

    from api.mock.mock_session import MockSession
    from api.models.application import Application
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User
    from api.services.application_deployment_service import ApplicationDeploymentService

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@x.com", user_name="t")
    infra = Infrastructure.objects.create(
        user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws", max_cpu=1024, max_memory=512,
    )
    env = Environment.objects.create(
        infrastructure=infra,
        ecr_repository_url="123456789012.dkr.ecr.us-east-1.amazonaws.com/launchpad-abc",
        ecs_task_execution_role_arn="arn:aws:iam::123456789012:role/exec",
    )
    application = Application.objects.create(
        user=user, infrastructure=infra, name="my-app", project_remote_url="https://github.com/o/r",
        project_branch="main", project_commit_hash="", envs={"API_KEY": SECRET_VALUE}, port=8080,
        alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
    )
    session = MockSession(region="us-east-1", account_id="123456789012", infra_id=str(infra.id))
    service = ApplicationDeploymentService()

    real_client = RealECSClient.__new__(RealECSClient)
    real_client.client = MagicMock()
    real_client.client.register_task_definition.return_value = {"taskDefinition": {"taskDefinitionArn": "arn:x"}}

    with caplog.at_level(logging.DEBUG), \
            patch("api.services.application_deployment_service.ECSClient", return_value=real_client):
        service._create_task_definition(session, application, env, resolved_sha="a" * 40)

    # A positive assertion first: prove the env-logging line (aws/ecs.py) actually fired,
    # so the negative assertion below means something rather than passing on an empty log.
    assert "API_KEY" in caplog.text
    assert SECRET_VALUE not in caplog.text


# ── R1: old/new code overlap corruption is detectable and repairable ────────────

def _rotate_command():
    module = importlib.import_module("api.management.commands.rotate_envs_encryption_key")
    return module.Command()


@pytest.mark.django_db
def test_json_quoted_fernet_token_raises_a_distinct_error(app):
    """Old (pre-H1) code's stock JSONField.from_db_value silently returns invalid JSON as
    the raw string (`except json.JSONDecodeError: return value`) instead of raising, so old
    code reading an already-encrypted column got the ciphertext back as a plain str; a
    later save() re-serializes that str as a JSON string, wrapping it in quotes. That
    specific corruption must be distinguishable from "wrong/missing key" or "tampered"."""
    raw = _raw_envs_column(app.id)
    json_quoted = json.dumps(raw)
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {_table()} SET envs = %s WHERE id = %s", [json_quoted, _prepped_id(app.id)])

    with pytest.raises(EnvsJSONQuotedTokenError):
        app.refresh_from_db()


@pytest.mark.django_db
def test_a_json_quoted_token_would_decrypt_if_we_let_it(app):
    """Pins the empirical finding behind the check above: `base64.urlsafe_b64decode`'s
    default leniency (`validate=False`) discards the two `"` a JSON-quote wrapping adds,
    so `MultiFernet.decrypt` called *directly* on a quoted token actually succeeds — this
    corruption does not, by itself, produce an undecryptable row. `EncryptedJSONField`
    still treats it as an error (the test above) precisely because that tolerance is a
    decoder implementation detail, not part of Fernet's contract; if a future
    `cryptography` release tightens base64 parsing, this half of the pair starts failing
    and says why."""
    from api.fields import multifernet

    raw = _raw_envs_column(app.id)
    json_quoted = json.dumps(raw).encode("ascii")
    assert multifernet().decrypt(json_quoted) == multifernet().decrypt(raw.encode("ascii"))


def test_not_migrated_error_for_plain_json_text():
    """The reachable case in practice: confirmed against real Postgres that a jsonb
    column, read through Django's own connection, hands EncryptedJSONField plain JSON
    *text* (a str), not a dict — psycopg2's default jsonb-to-object typecasting isn't in
    effect through Django, whose own JSONField.from_db_value does its own json.loads.
    So detection has to be "is this valid JSON", not "is this the wrong Python type"."""
    with pytest.raises(EnvsNotMigratedError):
        decrypt_value('{"NODE_ENV": "production"}')


def test_not_migrated_error_for_a_non_string_value():
    """Defensive fallback only — not known to be reachable via Django's postgres
    backend (see the test above), kept as a hard boundary in case a future driver/ORM
    version hands from_db_value something other than column text."""
    with pytest.raises(EnvsNotMigratedError):
        decrypt_value({"NODE_ENV": "production"})


@pytest.mark.django_db
def test_repair_command_unwraps_a_json_quoted_token(app):
    from io import StringIO

    from django.core.management import call_command

    raw = _raw_envs_column(app.id)
    json_quoted = json.dumps(raw)
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {_table()} SET envs = %s WHERE id = %s", [json_quoted, _prepped_id(app.id)])

    out = StringIO()
    call_command("repair_envs_encryption", stdout=out)
    assert "json_quoted_repaired=1" in out.getvalue()

    app.refresh_from_db()
    assert app.envs == {"NODE_ENV": "production", "API_KEY": SECRET_VALUE}
    # The repair also re-rotates onto the newest configured key, not just unquotes.
    repaired_raw = _raw_envs_column(app.id)
    assert repaired_raw != raw
    assert not repaired_raw.startswith('"')


@pytest.mark.django_db
def test_repair_command_encrypts_plaintext_json_left_by_old_code(app):
    from io import StringIO

    from django.core.management import call_command

    plaintext = json.dumps({"API_KEY": SECRET_VALUE, "NODE_ENV": "staging"})
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {_table()} SET envs = %s WHERE id = %s", [plaintext, _prepped_id(app.id)])

    out = StringIO()
    call_command("repair_envs_encryption", stdout=out)
    assert "plaintext_repaired=1" in out.getvalue()

    raw = _raw_envs_column(app.id)
    assert SECRET_VALUE not in raw
    app.refresh_from_db()
    assert app.envs == {"API_KEY": SECRET_VALUE, "NODE_ENV": "staging"}


@pytest.mark.django_db
def test_repair_command_leaves_healthy_rows_untouched(app):
    from io import StringIO

    from django.core.management import call_command

    raw_before = _raw_envs_column(app.id)
    out = StringIO()
    call_command("repair_envs_encryption", stdout=out)
    assert "ok=1" in out.getvalue()
    assert _raw_envs_column(app.id) == raw_before


@pytest.mark.django_db
def test_repair_command_reports_unrepairable_without_writing(app):
    from io import StringIO

    from django.core.management import call_command

    garbage = "not json and not a fernet token {{{"
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {_table()} SET envs = %s WHERE id = %s", [garbage, _prepped_id(app.id)])

    out, err = StringIO(), StringIO()
    call_command("repair_envs_encryption", stdout=out, stderr=err)
    assert "unrepairable=1" in out.getvalue()
    # The id in the message is whatever the raw cursor returned (hex, no dashes, on
    # SQLite; native uuid on Postgres) — the same value _prepped_id() computes.
    assert str(_prepped_id(app.id)) in err.getvalue()
    assert _raw_envs_column(app.id) == garbage


@pytest.mark.django_db
def test_repair_command_dry_run_does_not_write(app):
    from io import StringIO

    from django.core.management import call_command

    plaintext = json.dumps({"A": "b"})
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {_table()} SET envs = %s WHERE id = %s", [plaintext, _prepped_id(app.id)])

    out = StringIO()
    call_command("repair_envs_encryption", "--dry-run", stdout=out)
    assert "[dry-run]" in out.getvalue()
    assert "plaintext_repaired=1" in out.getvalue()
    assert _raw_envs_column(app.id) == plaintext


# ── R2: rotation is safe against a concurrent write (lost-update / CAS) ─────────

@pytest.mark.django_db
def test_rotate_reports_changed_concurrently_and_does_not_clobber_it(app):
    """Simulates the exact race: this command reads a row, then — before it writes back —
    something else (a real owner PATCH, or another rotation run) changes that row. A blind
    `.update(envs=<what we decrypted from the stale read>)` would silently discard the
    concurrent write. The CAS write must instead affect zero rows and leave the
    concurrently-written value alone."""
    with override_settings(APP_ENVS_ENCRYPTION_KEYS=(KEY_OLD,)):
        app.envs = {"API_KEY": SECRET_VALUE}
        app.save(update_fields=["envs"])
        stale_raw = _raw_envs_column(app.id)

        # A concurrent write lands after this command's read but before its write.
        app.envs = {"API_KEY": "rotated-out-from-under-us"}
        app.save(update_fields=["envs"])
        concurrent_raw = _raw_envs_column(app.id)
        assert concurrent_raw != stale_raw

        multi = MultiFernet([Fernet(KEY_NEW), Fernet(KEY_OLD)])
        command = _rotate_command()
        outcome = command._rotate_one(_table(), _prepped_id(app.id), stale_raw, multi, dry_run=False)

        assert outcome == "changed_concurrently"
        assert _raw_envs_column(app.id) == concurrent_raw  # untouched
        app.refresh_from_db()
        assert app.envs == {"API_KEY": "rotated-out-from-under-us"}


@pytest.mark.django_db
def test_rotate_uses_multifernet_rotate_not_decrypt_reencrypt_json(app):
    """MultiFernet.rotate() re-wraps the same ciphertext payload under a new key — it must
    not go through a decrypt-to-dict/json.dumps/re-encrypt round trip, which would be a
    second place a lost update (or a formatting difference) could sneak in."""
    with override_settings(APP_ENVS_ENCRYPTION_KEYS=(KEY_OLD,)):
        app.envs = {"API_KEY": SECRET_VALUE}
        app.save(update_fields=["envs"])
    raw = _raw_envs_column(app.id)

    multi = MultiFernet([Fernet(KEY_NEW), Fernet(KEY_OLD)])
    expected = multi.rotate(raw.encode("ascii")).decode("ascii")

    command = _rotate_command()
    outcome = command._rotate_one(_table(), _prepped_id(app.id), raw, multi, dry_run=False)
    assert outcome == "rotated"
    # Not byte-identical to `expected` (rotate() draws a fresh random IV each call), but
    # decrypts to the same plaintext under the newest key alone.
    stored = _raw_envs_column(app.id)
    assert Fernet(KEY_NEW).decrypt(stored.encode("ascii")) == Fernet(KEY_NEW).decrypt(expected.encode("ascii"))


# ── RECOMMENDED 1: refuse known-weak keys outside dev ────────────────────────────

def test_load_keys_refuses_dev_fallback_key_outside_dev():
    with pytest.raises(ValueError, match="must not include"):
        load_keys(DEV_FALLBACK_KEY, is_dev=False)


def test_load_keys_allows_the_dev_fallback_key_under_dev_mode():
    assert load_keys(DEV_FALLBACK_KEY, is_dev=True) == (DEV_FALLBACK_KEY,)


def test_dev_fallback_key_is_derived_deterministically():
    """Every MODE=dev process must derive the same key so ciphertext written by one
    process stays readable by another — re-derive independently (not just read the
    module-level constant) and compare, and confirm it's a structurally valid Fernet key."""
    from api.common.envs_encryption import _derive_dev_fallback_key

    assert _derive_dev_fallback_key() == DEV_FALLBACK_KEY
    Fernet(DEV_FALLBACK_KEY)


def test_settings_key_is_generated_not_a_committed_literal():
    """test_settings.py must not hand back a fixed key-shaped string — that is exactly
    the kind of committed secret load_keys refuses elsewhere, and what a secret scanner
    flags even for a test-only value. It should be a fresh, valid Fernet key, distinct
    from the derived dev-mode fallback."""
    import test_settings

    key = test_settings.APP_ENVS_ENCRYPTION_KEYS[0]
    Fernet(key)
    assert key != DEV_FALLBACK_KEY


# ── RECOMMENDED 2: .defer('envs') on paths that don't need it + startup canary ──

@pytest.mark.django_db
def test_delete_application_succeeds_when_envs_is_corrupted(app, monkeypatch):
    from unittest.mock import MagicMock

    from api.services.application_service import ApplicationService

    # delete_application acquires DeploymentLock (Redis-backed) before deleting — stub it
    # like test_app_delete_custom_domain_cleanup.py does, so this test has no live Redis
    # dependency (CI runs application-service tests with no Redis available).
    monkeypatch.setattr("api.services.application_service.DeploymentLock", MagicMock())

    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {_table()} SET envs = %s WHERE id = %s", ["garbage", _prepped_id(app.id)])

    service = ApplicationService()
    service.delete_application(str(app.user_id), str(app.id))  # must not raise

    from api.models.application import Application
    assert not Application.objects.filter(id=app.id).exists()


@pytest.mark.django_db
def test_list_applications_succeeds_when_one_apps_envs_is_corrupted(app):
    from api.models.application import Application
    from api.services.application_service import ApplicationService

    other = Application.objects.create(
        user_id=app.user_id, infrastructure_id=app.infrastructure_id, name="other-app",
        project_remote_url="https://github.com/o/r", project_branch="main",
        project_commit_hash="b" * 40, envs={"OK": "1"}, port=8080,
        alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
    )
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {_table()} SET envs = %s WHERE id = %s", ["garbage", _prepped_id(app.id)])

    service = ApplicationService()
    apps = list(service.get_user_applications(str(app.user_id), str(app.infrastructure_id)))

    names = {a.name for a in apps}  # accessing only fields the list endpoint serializes
    assert names == {"my-app", "other-app"}
    ids = {a.id for a in apps}
    assert other.id in ids


@pytest.mark.django_db
def test_get_by_id_defer_envs_does_not_decrypt_until_accessed(app):
    from api.repositories.application import ApplicationRepository

    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {_table()} SET envs = %s WHERE id = %s", ["garbage", _prepped_id(app.id)])

    fetched = ApplicationRepository().get_by_id(str(app.id), defer_envs=True)
    assert fetched.name == "my-app"  # does not raise

    with pytest.raises(EnvsDecryptionError):
        fetched.envs  # noqa: B018 — accessing a deferred field triggers its lazy load


def test_envs_decryption_canary_skips_an_empty_table(schema_db):
    from api.apps import _envs_decryption_canary

    _envs_decryption_canary()  # must not raise


@pytest.mark.django_db
def test_envs_decryption_canary_skips_pre_migration_rows(app, monkeypatch):
    # decrypt_value is imported inside the function body (`from api.fields import
    # decrypt_value`), so patching api.fields.decrypt_value is what the next call picks up.
    def _raise_not_migrated(raw):
        raise EnvsNotMigratedError("not migrated")

    monkeypatch.setattr("api.fields.decrypt_value", _raise_not_migrated)

    from api.apps import _envs_decryption_canary

    _envs_decryption_canary()  # must not raise


@pytest.mark.django_db
def test_envs_decryption_canary_skips_a_real_pre_migration_row(app):
    """Same as the test above but against the real decrypt_value, with a value shaped the
    way real Postgres actually produces it (plain JSON text, not a dict — see
    test_not_migrated_error_for_plain_json_text). The monkeypatched version above would
    not have caught the bug this test pins: an earlier version of decrypt_value only
    treated a non-str/bytes *type* as "not migrated", which real Postgres never sends
    through Django's connection, so every pre-migration row raised the generic
    EnvsDecryptionError instead and the canary crashed startup during the migration that
    was supposed to fix that."""
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {_table()} SET envs = %s WHERE id = %s", ['{"NODE_ENV": "production"}', _prepped_id(app.id)])

    from api.apps import _envs_decryption_canary

    _envs_decryption_canary()  # must not raise


@pytest.mark.django_db
def test_envs_decryption_canary_passes_with_a_healthy_row(app):
    from api.apps import _envs_decryption_canary

    _envs_decryption_canary()  # must not raise


@pytest.mark.django_db
def test_envs_decryption_canary_raises_and_logs_on_a_wrong_key(app, caplog):
    from api.apps import _envs_decryption_canary

    with override_settings(APP_ENVS_ENCRYPTION_KEYS=(KEY_NEW,)), \
            caplog.at_level(logging.CRITICAL), \
            pytest.raises(EnvsDecryptionError):
        _envs_decryption_canary()
    assert "refusing to start" in caplog.text


# ── RECOMMENDED 3: the PATCH view returns a generic 500, not a 400 with str(e) ──

@pytest.mark.django_db
def test_update_view_returns_generic_500_when_envs_is_corrupted(app, caplog):
    from types import SimpleNamespace

    from rest_framework.test import APIRequestFactory, force_authenticate

    from api.views.application import ApplicationUpdateView

    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {_table()} SET envs = %s WHERE id = %s", ["garbage", _prepped_id(app.id)])

    view = ApplicationUpdateView.as_view()
    req = APIRequestFactory().patch(f"/api/v1/applications/{app.id}/", {"description": "new"}, format="json")
    force_authenticate(req, user=SimpleNamespace(id=str(app.user_id), is_authenticated=True))

    with caplog.at_level(logging.ERROR):
        resp = view(req, pk=str(app.id))

    assert resp.status_code == 500
    assert resp.data["error"] == "Internal server error"  # never str(e)
    assert str(app.id) in caplog.text


# ── RECOMMENDED 4: to_python raises ValidationError, not an internal exception ──

def test_to_python_raises_validation_error_for_undecryptable_input():
    field = EncryptedJSONField()
    with pytest.raises(ValidationError):
        field.to_python("not-a-fernet-token-at-all")


def test_to_python_passes_through_none_and_dicts():
    field = EncryptedJSONField()
    assert field.to_python(None) is None
    assert field.to_python({"a": "b"}) == {"a": "b"}
