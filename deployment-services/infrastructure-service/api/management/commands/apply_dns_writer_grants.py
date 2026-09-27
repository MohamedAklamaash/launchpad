import logging
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)

GRANTS_SQL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "services" / "platform_dns" / "sql" / "dns_writer_grants.sql"
)


class Command(BaseCommand):
    help = (
        "Apply the least-privilege GRANT statements for the dns_writer Postgres role "
        "(api/services/platform_dns/sql/dns_writer_grants.sql). Run once, after migrations "
        "have created the tables being granted on — the role itself has no table to grant "
        "on before that. Postgres only; a no-op with a warning on any other backend "
        "(sqlite in tests, for instance)."
    )

    def handle(self, *args, **options):
        from django.db import connection

        if connection.vendor != "postgresql":
            self.stdout.write(self.style.WARNING(
                f"apply_dns_writer_grants: backend is {connection.vendor!r}, not "
                "postgresql — nothing to do."
            ))
            return

        if not GRANTS_SQL_PATH.exists():
            raise CommandError(f"grants SQL file not found: {GRANTS_SQL_PATH}")

        sql = GRANTS_SQL_PATH.read_text()
        with connection.cursor() as cursor:
            cursor.execute(sql)

        self.stdout.write(self.style.SUCCESS(
            f"apply_dns_writer_grants: applied {GRANTS_SQL_PATH.name}"
        ))
