"""Model fields shared across `api.models`.

`EncryptedJSONField` backs `Application.envs` (H1) — see `api/common/envs_encryption.py`
for key loading and migration `0037_encrypt_application_envs` for the backfill of rows
written before this field existed.
"""
import json

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class EnvsDecryptionError(ValueError):
    """A value in an `EncryptedJSONField` column did not decrypt under any key in
    `settings.APP_ENVS_ENCRYPTION_KEYS`.

    Raised instead of returning the raw column value or guessing it might be plaintext:
    after the backfill migration, anything in that column that isn't ciphertext under a
    currently configured key is either data encrypted under a key that has since been
    removed, or a bug/tampering — never a plaintext value to silently fall back to. The
    message never includes the undecryptable payload.
    """


class EnvsNotMigratedError(EnvsDecryptionError):
    """The column holds valid JSON text, not ciphertext.

    Two situations look identical from the value alone, and are treated the same: migration
    `0037_encrypt_application_envs` hasn't run against this database yet (a genuinely
    pre-migration row), or something wrote plaintext into an already-migrated column (R1,
    security review — `repair_envs_encryption` is the tool for that case specifically).
    Either way, it isn't a key problem: callers that need to tell "wrong/missing key" apart
    from "this row was never encrypted" (the startup canary in `api/apps.py`) catch this
    separately and treat it as "nothing to verify yet", not a startup failure. Confirmed
    empirically against real Postgres that a `jsonb` column reads through Django's
    connection as plain JSON text, not a `dict` — psycopg2's default jsonb-to-object
    typecasting is not what's in effect here, `JSONField.from_db_value` does its own
    `json.loads` — so detection is "is this valid JSON", not "is this the wrong Python
    type" (kept below as a defensive fallback that isn't known to trigger in practice).
    """


class EnvsJSONQuotedTokenError(EnvsDecryptionError):
    """The column holds a real Fernet token wrapped in JSON string quotes — the
    signature of an old (pre-H1) process overlapping with this migration or a key
    rotation. Old code's stock `django.db.models.JSONField.from_db_value` silently
    returns invalid JSON as the raw string instead of raising
    (`except json.JSONDecodeError: return value`), so old code reading an
    already-encrypted column got the ciphertext back as a plain Python `str`; a later
    `save()` re-serialized that `str` as a JSON string, wrapping it in quotes. Repair with
    `manage.py repair_envs_encryption` — never treat this the same as a wrong/missing key,
    since the underlying ciphertext is fine once unquoted.
    """


def multifernet() -> MultiFernet:
    """Public: migration `0037`, `rotate_envs_encryption_key`, and
    `repair_envs_encryption` all need the same key list this field encrypts/decrypts
    with, and re-deriving it in each place would risk drift."""
    return MultiFernet([Fernet(key) for key in settings.APP_ENVS_ENCRYPTION_KEYS])


def encrypt_value(value) -> str | None:
    if value is None:
        return None
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return multifernet().encrypt(payload).decode("ascii")


def looks_like_json_quoted_token(raw: str, fernet: MultiFernet) -> bool:
    """True when `raw` is a real Fernet token wrapped in JSON string quotes.

    `base64.urlsafe_b64decode` (which `Fernet.decrypt` uses, `validate=False` by default)
    silently discards characters outside the base64 alphabet — including the two `"` this
    wrapping adds — so a quoted token actually decrypts fine *if asked directly*. Checked
    and confirmed in `test_a_json_quoted_token_would_decrypt_if_we_let_it`. Relying on that
    tolerance instead of detecting the shape would mean a malformed column value only stays
    readable by accident of a stdlib default that isn't part of Fernet's documented
    contract — this check runs before the real decrypt attempt precisely so a quoted value
    is always treated as the corruption it is, never silently passed through.
    """
    if not (raw.startswith('"') and raw.endswith('"')):
        return False
    try:
        inner = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(inner, str):
        return False
    try:
        fernet.decrypt(inner.encode("ascii"))
    except InvalidToken:
        return False
    return True


def _is_plain_json(raw) -> bool:
    try:
        json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False
    return True


def decrypt_value(raw) -> object:
    if raw is None:
        return None
    if not isinstance(raw, (str, bytes)):
        # Defensive fallback, not known to trigger via Django's postgres backend (see
        # EnvsNotMigratedError) — kept as a hard boundary against a driver/ORM version
        # that hands from_db_value something other than column text.
        raise EnvsNotMigratedError(
            "envs column value is not text and not a string — cannot tell whether this "
            "is pre-migration data or something else"
        )
    fernet = multifernet()
    if isinstance(raw, str) and looks_like_json_quoted_token(raw, fernet):
        raise EnvsJSONQuotedTokenError(
            "envs column holds a Fernet token wrapped in JSON string quotes — repair "
            "with `manage.py repair_envs_encryption`"
        )
    try:
        payload = fernet.decrypt(raw.encode("ascii") if isinstance(raw, str) else raw)
    except InvalidToken as exc:
        if _is_plain_json(raw):
            raise EnvsNotMigratedError(
                "envs column holds plain JSON text, not ciphertext — either migration "
                "0037_encrypt_application_envs hasn't run against this database yet, or "
                "something wrote plaintext into an already-migrated column (see "
                "repair_envs_encryption)"
            ) from exc
        raise EnvsDecryptionError(
            "column value does not decrypt under any configured APP_ENVS_ENCRYPTION_KEYS"
        ) from exc
    return json.loads(payload)


class EncryptedJSONField(models.TextField):
    """A JSON-serializable value (a dict, for `Application.envs`) stored as Fernet
    ciphertext. Every read decrypts, every write encrypts under the newest configured key
    — `MultiFernet` tries every configured key on decrypt, so `rotate_envs_encryption_key`
    can move existing rows onto a newer key without a flag day.

    Deliberately a `TextField`, not a `JSONField`: an encrypted value is opaque text, so
    DB-level JSON queries (`filter(envs__key=...)`) are impossible by construction — nothing
    in this codebase queries into `envs`'s structure today.
    """

    description = "Fernet-encrypted JSON"

    def get_prep_value(self, value):
        return encrypt_value(value)

    def from_db_value(self, value, expression, connection):
        return decrypt_value(value)

    def to_python(self, value):
        # Reached by form/admin validation (full_clean()), never the DB read path
        # (from_db_value handles that) — Django field convention is ValidationError for
        # bad input here, not an arbitrary exception surfacing as an unhandled 500.
        if value is None or isinstance(value, (dict, list)):
            return value
        try:
            return decrypt_value(value)
        except EnvsDecryptionError as exc:
            raise ValidationError(str(exc)) from exc

    def value_to_string(self, obj):
        # dumpdata/serializers: keep ciphertext in any fixture output, never the decrypted
        # value — loaddata round-trips fine since `to_python` accepts ciphertext.
        return self.get_prep_value(self.value_from_object(obj))

    def formfield(self, **kwargs):
        # Application is registered in Django admin with no custom ModelForm. Without
        # this, the base TextField gives admin a plain CharField/Textarea: it would
        # display the decrypted dict via str(dict) and save whatever text comes back as
        # a JSON *string* wrapping that text, silently turning envs into a string instead
        # of a dict on the next admin save — corrupting it for every later reader. A
        # JSONField form field keeps admin editing valid JSON in and a dict out, exactly
        # like `models.JSONField.formfield()` already does for every other JSON column.
        return super().formfield(**{"form_class": forms.JSONField, **kwargs})
