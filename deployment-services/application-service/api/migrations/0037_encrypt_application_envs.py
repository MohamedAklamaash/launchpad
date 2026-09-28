"""H1: encrypt Application.envs at rest.

Two steps, one transaction (Django migrations are atomic by default on Postgres):

1. `AlterField` changes the column from `jsonb` to `text`. Postgres's own ALTER COLUMN
   TYPE cast (`USING envs::text`) only changes representation — the column still holds
   plain JSON text after this step, just as a string instead of a jsonb value.
2. `encrypt_existing_envs` then walks every row with a raw cursor (never through the ORM:
   the model's field is already `EncryptedJSONField` by this point in the migration graph,
   so `Application.objects` would try to decrypt what is still plaintext JSON text) and
   replaces that plaintext JSON text with its Fernet ciphertext. The JSON text from the
   cast is encrypted byte-for-byte — no `json.loads`/`json.dumps` round trip — since
   `EncryptedJSONField.from_db_value` only needs valid JSON text after decrypting, not any
   particular formatting of it.

Because both steps are one migration, there is no window — inside or outside this
process — where the column is column-type `text` and still holds plaintext outside an
uncommitted transaction.

Reverse order matters: unapplying runs operations in reverse, so `decrypt_existing_envs`
(ciphertext -> plaintext JSON text) runs first, restoring valid JSON text, before the
`AlterField` reverse casts the column back to `jsonb`. The reverse needs
`APP_ENVS_ENCRYPTION_KEYS` to still include whatever key encrypted each row — do not
remove a key from that setting until every row that could have used it has been
re-encrypted under a newer one (`rotate_envs_encryption_key`).
"""
from django.db import migrations

import api.fields


def encrypt_existing_envs(apps, schema_editor):
    from api.fields import multifernet

    Application = apps.get_model("api", "Application")
    table = Application._meta.db_table
    fernet = multifernet()
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT id, envs FROM {table} WHERE envs IS NOT NULL")
        rows = cursor.fetchall()
        for row_id, raw_json_text in rows:
            token = fernet.encrypt(raw_json_text.encode("utf-8"))
            cursor.execute(f"UPDATE {table} SET envs = %s WHERE id = %s", [token.decode("ascii"), row_id])


def decrypt_existing_envs(apps, schema_editor):
    from api.fields import multifernet

    Application = apps.get_model("api", "Application")
    table = Application._meta.db_table
    fernet = multifernet()
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT id, envs FROM {table} WHERE envs IS NOT NULL")
        rows = cursor.fetchall()
        for row_id, ciphertext in rows:
            payload = fernet.decrypt(ciphertext.encode("ascii"))
            cursor.execute(f"UPDATE {table} SET envs = %s WHERE id = %s", [payload.decode("utf-8"), row_id])


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0036_infrastructure_exited_at"),
    ]

    operations = [
        migrations.AlterField(
            model_name="application",
            name="envs",
            field=api.fields.EncryptedJSONField(blank=True, default=dict, null=True),
        ),
        migrations.RunPython(encrypt_existing_envs, decrypt_existing_envs),
    ]
