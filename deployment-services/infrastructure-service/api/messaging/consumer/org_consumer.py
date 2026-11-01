import json
import logging
import threading
import time
import uuid

from api.common.envs.application import app_config
from api.repositories.organization import OrganizationRepository, PendingOrganization
from api.services.org_permissions import OrgPermissions
from django.conf import settings
from django.db import connection, transaction
from django.db.utils import OperationalError, ProgrammingError
from shared.enums.org_role import OrgRole
from shared.resilience import ResilientPikaConsumer

logger = logging.getLogger(__name__)

ORG_CREATED_ROUTING_KEY = "org.created"
ORG_MEMBERSHIP_CHANGED_ROUTING_KEY = "org.membership.changed"
ORG_MEMBERSHIP_REMOVED_ROUTING_KEY = "org.membership.removed"
ORG_INVITATION_ACCEPTED_ROUTING_KEY = "org.invitation.accepted"


class OrgEventConsumer:
    """Consumes identity-services' org.events exchange and mirrors Organization/Membership
    read-model tables (F9 ticket 4, plan/F9-teams-and-orgs.md). Bound to the `org.#`
    wildcard rather than one queue per routing key: every event for a given org then lands
    in the same queue in publish order, which shrinks (but does not eliminate — see
    PendingOrganization) the window where a membership event for an org still outruns
    org.created.

    Version-rejection rule: every payload's `version` is the per-Membership monotonic
    counter (host_readiness_version's pattern) — a payload whose version is not strictly
    greater than what's already stored is logged and ACKed, never applied, never treated
    as an error. `metadata.version` on the outer envelope is the schema version (always 1)
    and is never read here.
    """

    EXCHANGE_NAME = "org.events"
    ROUTING_KEY = "org.#"
    QUEUE_NAME = "infrastructure-service.org-events"
    # Bounded retry for "organization not yet materialized" — mirrors
    # PendingInvitedInfrastructure's budget (application-service api/repositories/user.py).
    MAX_RETRIES = 3

    def __init__(self):
        self.org_repo = OrganizationRepository()
        self._retry_counts: dict = {}
        self.consumer = ResilientPikaConsumer(
            url=app_config.rabbitmq_url,
            exchange=self.EXCHANGE_NAME,
            queue=self.QUEUE_NAME,
            routing_key=self.ROUTING_KEY,
            name="infrastructure-service-org-consumer",
            prefetch_count=1,
        )

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        # ProgrammingError (schema/SQL mismatch) is permanent — requeueing it against a
        # DLX-less, prefetch=1 queue is a sleepless redelivery loop that never drains (see
        # AuthEventConsumer._is_transient in api/messaging/consumer/consumer.py).
        return isinstance(exc, OperationalError)

    def callback(self, ch, method, properties, body):
        correlation_id = (
            properties.correlation_id
            if properties and properties.correlation_id
            else str(uuid.uuid4())
        )
        log = logger.getChild("org_event")
        routing_key = method.routing_key

        try:
            event = json.loads(body)
        except json.JSONDecodeError as exc:
            log.exception(
                "JSON decode failed — discarding unparseable message",
                extra={"correlation_id": correlation_id, "error": str(exc)},
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        payload = event.get("payload", {})
        organization_id = payload.get("organization_id")
        version = payload.get("version")

        if not organization_id or not isinstance(version, int):
            log.warning(
                "org event missing/invalid organization_id or version — discarding",
                extra={"correlation_id": correlation_id, "routing_key": routing_key, "payload": payload},
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        account_id = payload.get("account_id")
        retry_key = (routing_key, organization_id, account_id)

        if routing_key == ORG_CREATED_ROUTING_KEY:
            owner_account_id = payload.get("owner_account_id")
            if not owner_account_id:
                log.warning(
                    "org.created missing owner_account_id — discarding",
                    extra={"correlation_id": correlation_id, "organization_id": organization_id},
                )
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return
        elif routing_key in (ORG_MEMBERSHIP_CHANGED_ROUTING_KEY, ORG_INVITATION_ACCEPTED_ROUTING_KEY):
            role = payload.get("role")
            previous_owner_version = payload.get("previous_owner_version")
            if previous_owner_version is not None and not isinstance(previous_owner_version, int):
                log.warning(
                    "org membership event carries a non-integer previous_owner_version — discarding",
                    extra={"correlation_id": correlation_id, "organization_id": organization_id},
                )
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return
            if not account_id or role not in OrgRole.values:
                log.warning(
                    "org membership event missing account_id or carries an unrecognized role — discarding",
                    extra={"correlation_id": correlation_id, "organization_id": organization_id, "role": role},
                )
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return
        elif routing_key == ORG_MEMBERSHIP_REMOVED_ROUTING_KEY:
            if not account_id:
                log.warning(
                    "org.membership.removed missing account_id — discarding",
                    extra={"correlation_id": correlation_id, "organization_id": organization_id},
                )
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return
        else:
            log.warning(
                "unrecognized org.events routing key — discarding",
                extra={"correlation_id": correlation_id, "routing_key": routing_key},
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        try:
            connection.close()
            demoted = []
            with transaction.atomic():
                if routing_key == ORG_CREATED_ROUTING_KEY:
                    demoted = self.org_repo.apply_org_created(
                        organization_id, payload.get("owner_account_id"), version,
                    )
                elif routing_key in (ORG_MEMBERSHIP_CHANGED_ROUTING_KEY, ORG_INVITATION_ACCEPTED_ROUTING_KEY):
                    demoted = self.org_repo.upsert_membership(
                        organization_id, account_id, payload.get("role"), version,
                        previous_owner_account_id=payload.get("previous_owner_account_id"),
                        previous_owner_version=payload.get("previous_owner_version"),
                    )
                else:
                    self.org_repo.remove_membership(organization_id, account_id, version)

            # F9 ticket 5: evict the cached role right after the DB write durably
            # commits (we're past the `with transaction.atomic()` block), so a role
            # change/removal is reflected sub-second instead of waiting out the cache's
            # TTL backstop. Best-effort — see OrgPermissions.evict/org_role_cache: a
            # Redis failure here must never nack or fail an event whose DB write already
            # succeeded.
            if routing_key == ORG_CREATED_ROUTING_KEY:
                OrgPermissions.evict(organization_id, payload.get("owner_account_id"))
            else:
                OrgPermissions.evict(organization_id, account_id)
            for displaced_account_id in demoted or []:
                OrgPermissions.evict(organization_id, displaced_account_id)

            self._retry_counts.pop(retry_key, None)
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except PendingOrganization as exc:
            retry_count = self._retry_counts.get(retry_key, 0)
            if retry_count >= self.MAX_RETRIES:
                log.warning(
                    "organization still not materialized after max retries — giving up on this event",
                    extra={"correlation_id": correlation_id, "organization_id": exc.organization_id},
                )
                self._retry_counts.pop(retry_key, None)
                ch.basic_ack(delivery_tag=method.delivery_tag)
            else:
                self._retry_counts[retry_key] = retry_count + 1
                delay = min(2 ** retry_count, 5)
                log.info(
                    "organization not materialized yet — requeueing (attempt %d/%d, delay %ds)",
                    retry_count + 1, self.MAX_RETRIES, delay,
                    extra={"correlation_id": correlation_id, "organization_id": exc.organization_id},
                )
                # H6 (see application-service's InfraExitedEventConsumer): NOT time.sleep —
                # starves the pika connection's heartbeat.
                ch.connection.sleep(delay)
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

        except Exception as exc:
            transient = self._is_transient(exc)
            log.exception(
                "Error processing org event — %s",
                "NACKing with requeue (transient)" if transient else "NACKing without requeue (permanent)",
                extra={
                    "correlation_id": correlation_id, "organization_id": organization_id,
                    "error": str(exc), "exc_type": type(exc).__name__,
                },
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=transient)

    def start(self):
        self.consumer.start(self.callback)

    def stop(self):
        self.consumer.stop()

    def close(self):
        self.stop()


def _wait_for_db(max_wait: int = 60, interval: int = 3) -> bool:
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        try:
            connection.ensure_connection()
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM api_user LIMIT 1")
            return True
        except (OperationalError, ProgrammingError):
            time.sleep(interval)
        finally:
            connection.close()
    return False


def start_org_event_consumer():
    """Entry point called from api/apps.py's ready(). Gated on ORGS_ENABLED — with the
    flag off, nothing here opens a socket to the broker."""
    if not settings.ORGS_ENABLED:
        logger.info("ORGS_ENABLED is False — org.events consumer not started")
        return

    def _run():
        try:
            if not _wait_for_db():
                return
            logger.info("Initializing Infrastructure Service OrgEventConsumer…")
            OrgEventConsumer().start()
        except Exception:
            logger.exception("Infrastructure Service OrgEventConsumer crashed")

    threading.Thread(target=_run, name="InfraOrgConsumer", daemon=True).start()
