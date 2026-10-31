"""Startup guard for turning ORGS_ENABLED back off.

With the flag on, an org admin can create an infrastructure; `Infrastructure.user` is then
that admin. The flag-off checks treat `user` as the owner, so rolling the flag back would
give the admin owner-only powers (nuke, exit, costs) and lock out the real owner. Rolling
back after such creates is unsupported: the service refuses to start until the rows are
handled by hand.
"""
import logging
import sys

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.utils import OperationalError, ProgrammingError

logger = logging.getLogger(__name__)

SCHEMA_COMMANDS = {"migrate", "makemigrations", "showmigrations", "sqlmigrate"}


def enforce_flag_off_safety() -> None:
    if settings.ORGS_ENABLED or SCHEMA_COMMANDS & set(sys.argv):
        return
    from api.models.infrastructure import Infrastructure

    try:
        offending = Infrastructure.objects.filter(created_by_non_owner_under_orgs=True).count()
    except (OperationalError, ProgrammingError):
        logger.info("org flag guard skipped: infrastructure table not available yet")
        return
    if offending:
        raise ImproperlyConfigured(
            f"ORGS_ENABLED is off but {offending} infrastructure(s) were created by a non-owner "
            "org member while it was on. Their creator would become the owner under the legacy "
            "checks. Turning the flag back off after flag-on creates is unsupported; re-enable "
            "ORGS_ENABLED, or reassign these rows by hand first."
        )
