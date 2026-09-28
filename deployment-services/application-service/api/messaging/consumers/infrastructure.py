import json
import logging
import time
import uuid

from api.common.envs.application import app_config
from api.common.host_url import is_valid_dns_label
from api.repositories.infrastructure import InfrastructureRepository
from django.core.exceptions import ObjectDoesNotExist
from django.db import OperationalError, connection, transaction
from shared.resilience import ResilientPikaConsumer

logger = logging.getLogger(__name__)


class InfraEventConsumer:
    """Consume infrastructure.created events from RabbitMQ and sync local database."""

    EXCHANGE_NAME = "infrastructure.events"
    ROUTING_KEY = "infrastructure.created"
    QUEUE_NAME = "application-service.infra-events"
    MAX_RETRIES = 10

    def __init__(self):
        self.infra_repo = InfrastructureRepository()
        self._retry_counts: dict = {}
        self.consumer = ResilientPikaConsumer(
            url=app_config.rabbitmq_url,
            exchange=self.EXCHANGE_NAME,
            queue=self.QUEUE_NAME,
            routing_key=self.ROUTING_KEY,
            name="application-service-infra-consumer",
            prefetch_count=1,
        )

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        """
            Return True for errors that are safe to retry (NACK with requeue=True).
        """
        return isinstance(exc, (ObjectDoesNotExist, OperationalError))

    def callback(self, ch, method, properties, body):
        """
        Process received infrastructure.created events.
        """
        correlation_id = (
            properties.correlation_id
            if properties and properties.correlation_id
            else str(uuid.uuid4())
        )
        log = logger.getChild("infra_event")

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

        infra_id = payload.get("id") or payload.get("infra_id")
        user_id = payload.get("user_id")

        log.info(
            "Received infrastructure.created event",
            extra={
                "correlation_id": correlation_id,
                "infra_id": infra_id,
                "user_id": user_id,
                "event_type": event.get("type"),
            },
        )

        if not infra_id or not user_id:
            log.warning(
                "infra event missing required fields id/user_id — discarding",
                extra={"correlation_id": correlation_id, "payload": payload},
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        try:
            connection.close()
            with transaction.atomic():
                self.infra_repo.upsert_infrastructure(
                    {
                        "id": infra_id,
                        "user_id": user_id,
                        "name": payload.get("name") or "",
                        "cloud_provider": payload.get("cloud_provider") or "",
                        # Absent/null in payloads from pre-EKS producers; the repo skips
                        # invalid values so the column keeps its default.
                        "compute_type": payload.get("compute_type"),
                        "max_cpu": payload.get("max_cpu", 0),
                        "max_memory": payload.get("max_memory", 0),
                        "code": payload.get("code"),
                        "is_cloud_authenticated": payload.get("is_cloud_authenticated", False),
                        "is_mock": payload.get("is_mock", False),
                        "metadata": payload.get("metadata"),
                    }
                )

            log.info(
                "infrastructure upserted successfully — ACKing",
                extra={"correlation_id": correlation_id, "infra_id": infra_id},
            )
            self._retry_counts.pop(infra_id, None)
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as exc:
            transient = self._is_transient(exc)
            if transient:
                # Expected ordering/staleness (owner not synced yet, or a stale infra whose owner
                # never will be). Log a clean WARNING and retry (bounded) — no scary traceback.
                retry_count = self._retry_counts.get(infra_id, 0)
                if retry_count >= self.MAX_RETRIES:
                    log.warning(
                        "infrastructure event unresolved after max retries — discarding (likely stale infra)",
                        extra={"correlation_id": correlation_id, "infra_id": infra_id, "error": str(exc)},
                    )
                    self._retry_counts.pop(infra_id, None)
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                else:
                    self._retry_counts[infra_id] = retry_count + 1
                    delay = min(2 ** retry_count, 30)
                    log.warning(
                        "infrastructure event deferred — requeueing (attempt %d/%d, delay %ds)",
                        retry_count + 1, self.MAX_RETRIES, delay,
                        extra={"correlation_id": correlation_id, "infra_id": infra_id, "error": str(exc)},
                    )
                    time.sleep(delay)
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            else:
                log.exception(
                    "Error persisting infrastructure event — NACKing without requeue (permanent)",
                    extra={"correlation_id": correlation_id, "infra_id": infra_id, "error": str(exc)},
                )
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    def start(self):
        """Start consuming messages."""
        self.consumer.start(self.callback)

    def stop(self):
        """Stop consuming messages."""
        self.consumer.stop()

    def close(self):
        """Close connection."""
        self.stop()


class InfraUpdatedEventConsumer:
    """Consume infrastructure.updated events from RabbitMQ and sync local database."""

    EXCHANGE_NAME = "infrastructure.events"
    ROUTING_KEY = "infrastructure.updated"
    QUEUE_NAME = "application-service.infra-updated-events"
    MAX_RETRIES = 10

    def __init__(self):
        self.infra_repo = InfrastructureRepository()
        self._retry_counts: dict = {}
        self.consumer = ResilientPikaConsumer(
            url=app_config.rabbitmq_url,
            exchange=self.EXCHANGE_NAME,
            queue=self.QUEUE_NAME,
            routing_key=self.ROUTING_KEY,
            name="application-service-infra-updated-consumer",
            prefetch_count=1,
        )

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        return isinstance(exc, (ObjectDoesNotExist, OperationalError))

    def callback(self, ch, method, properties, body):
        """Process infrastructure.updated events."""
        correlation_id = (
            properties.correlation_id
            if properties and properties.correlation_id
            else str(uuid.uuid4())
        )
        log = logger.getChild("infra_updated_event")

        try:
            event = json.loads(body)
        except json.JSONDecodeError:
            log.error("JSON decode failed", extra={"correlation_id": correlation_id})
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        payload = event.get("payload", {})
        infra_id = payload.get("id") or payload.get("infra_id")

        log.info(
            "Received infrastructure.updated event",
            extra={"correlation_id": correlation_id, "infra_id": infra_id},
        )

        if not infra_id:
            log.warning("Missing infra_id — discarding")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        try:
            connection.close()
            infra = self.infra_repo.get_infrastructure(infra_id)
            if infra is None:
                # infrastructure.updated and .created travel on separate queues with no ordering
                # guarantee. If the update lands before the create is materialized, retry (bounded)
                # instead of ACK-dropping — otherwise stale max_cpu/max_memory limits stick forever.
                retry_count = self._retry_counts.get(infra_id, 0)
                if retry_count >= self.MAX_RETRIES:
                    log.error(
                        "Infrastructure not materialized after max retries — discarding update",
                        extra={"correlation_id": correlation_id, "infra_id": infra_id, "retries": retry_count},
                    )
                    self._retry_counts.pop(infra_id, None)
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                else:
                    self._retry_counts[infra_id] = retry_count + 1
                    delay = min(2 ** retry_count, 30)
                    log.warning(
                        "Infrastructure not materialized yet — NACKing update with requeue (attempt %d/%d, delay %ds)",
                        retry_count + 1, self.MAX_RETRIES, delay,
                        extra={"correlation_id": correlation_id, "infra_id": infra_id},
                    )
                    time.sleep(delay)
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                return

            with transaction.atomic():
                update_fields = []
                if "name" in payload:
                    infra.name = payload["name"]
                    update_fields.append("name")
                if "max_cpu" in payload:
                    infra.max_cpu = payload["max_cpu"]
                    update_fields.append("max_cpu")
                if "max_memory" in payload:
                    infra.max_memory = payload["max_memory"]
                    update_fields.append("max_memory")
                if update_fields:
                    infra.save(update_fields=update_fields)
                    log.info(f"Infrastructure {infra_id} updated")

            self._retry_counts.pop(infra_id, None)
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as exc:
            transient = self._is_transient(exc)
            log.exception(
                "Error processing infrastructure.updated event",
                extra={"correlation_id": correlation_id, "infra_id": infra_id},
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=transient)

    def start(self):
        self.consumer.start(self.callback)

    def stop(self):
        self.consumer.stop()

    def close(self):
        self.stop()


class HostReadinessEventConsumer:
    """Consume infrastructure.host_readiness_updated and mirror it onto the read-model
    (F1b part 3a — see infrastructure-service's api/services/host_readiness.py).

    A pure state mirror: this consumer only ever writes read-model fields, never anything
    that grants access or bypasses a check — the payload must not be trusted for
    authorization. Every field is a fresh snapshot, not a diff, so a message applied out of
    order only risks a stale value briefly winning over a fresher one; `host_readiness_version`
    (a per-infra monotonic counter minted by infrastructure-service, not a wall clock —
    RECOMMENDED item 2, security review) closes that window. Required: a payload missing it
    is discarded rather than treated as "always current"."""

    EXCHANGE_NAME = "infrastructure.events"
    ROUTING_KEY = "infrastructure.host_readiness_updated"
    QUEUE_NAME = "application-service.host-readiness-events"
    MAX_RETRIES = 10

    def __init__(self):
        self._retry_counts: dict = {}
        self.consumer = ResilientPikaConsumer(
            url=app_config.rabbitmq_url,
            exchange=self.EXCHANGE_NAME,
            queue=self.QUEUE_NAME,
            routing_key=self.ROUTING_KEY,
            name="application-service-host-readiness-consumer",
            prefetch_count=1,
        )

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        return isinstance(exc, (ObjectDoesNotExist, OperationalError))

    def callback(self, ch, method, properties, body):
        correlation_id = (
            properties.correlation_id
            if properties and properties.correlation_id
            else str(uuid.uuid4())
        )
        log = logger.getChild("host_readiness_event")

        try:
            event = json.loads(body)
        except json.JSONDecodeError:
            log.error("JSON decode failed — discarding", extra={"correlation_id": correlation_id})
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        payload = event.get("payload", {})
        infra_id = payload.get("infra_id")
        if not infra_id:
            log.warning("host_readiness event missing infra_id — discarding", extra={"correlation_id": correlation_id})
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        # RECOMMENDED item 2 (security review): required, not just preferred — a payload
        # missing this counter cannot be ordered against what's already stored, and treating
        # "missing" as "always current" (the old occurred_at-is-None behavior) would let a
        # stripped or malformed field bypass the staleness check entirely.
        incoming_version = payload.get("host_readiness_version")
        if not isinstance(incoming_version, int):
            log.error(
                "host_readiness event missing/invalid host_readiness_version — discarding",
                extra={"correlation_id": correlation_id, "infra_id": infra_id},
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        try:
            from api.models.infrastructure import Infrastructure

            connection.close()

            infra = Infrastructure.objects.filter(id=infra_id).first()
            if infra is None:
                retry_count = self._retry_counts.get(infra_id, 0)
                if retry_count >= self.MAX_RETRIES:
                    log.warning(
                        "host_readiness event unresolved after max retries — discarding (likely stale infra)",
                        extra={"correlation_id": correlation_id, "infra_id": infra_id},
                    )
                    self._retry_counts.pop(infra_id, None)
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                else:
                    self._retry_counts[infra_id] = retry_count + 1
                    delay = min(2 ** retry_count, 30)
                    log.warning(
                        "host_readiness event deferred — infra not synced yet (attempt %d/%d, delay %ds)",
                        retry_count + 1, self.MAX_RETRIES, delay,
                        extra={"correlation_id": correlation_id, "infra_id": infra_id},
                    )
                    time.sleep(delay)
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                return

            with transaction.atomic():
                # Never let an older snapshot overwrite a fresher one — this service's
                # several publishers (a terraform apply, the TLS re-check tick, the DNS
                # writer's own converge loop) give no cross-publisher ordering guarantee.
                # host_readiness_version is a per-infra monotonic counter minted by
                # infrastructure-service under a row lock, immune to clock skew between
                # those publishers the way a wall-clock timestamp is not.
                if incoming_version <= infra.host_readiness_version:
                    log.info(
                        "host_readiness event is stale — discarding without applying",
                        extra={"correlation_id": correlation_id, "infra_id": infra_id},
                    )
                else:
                    update_fields = ["dns_synced", "https_ready", "host_readiness_version"]
                    infra.dns_synced = bool(payload.get("dns_synced", False))
                    infra.https_ready = bool(payload.get("https_ready", False))
                    infra.host_readiness_version = incoming_version
                    # R4 (security review): write-once, same as upsert_infrastructure — a
                    # stored non-null dns_label that disagrees with the incoming payload is
                    # refused and logged rather than overwritten. This field feeds directly
                    # into every hostname this service builds (api/common/host_url.py).
                    incoming_dns_label = payload.get("dns_label")
                    if incoming_dns_label and not is_valid_dns_label(incoming_dns_label):
                        log.error(
                            "host_readiness event carries a malformed dns_label — ignoring",
                            extra={"correlation_id": correlation_id, "infra_id": infra_id},
                        )
                    elif incoming_dns_label:
                        if infra.dns_label is None:
                            infra.dns_label = incoming_dns_label
                            update_fields.append("dns_label")
                        elif infra.dns_label != incoming_dns_label:
                            log.error(
                                "host_readiness event dns_label disagrees with the stored "
                                "value — refusing to overwrite",
                                extra={
                                    "correlation_id": correlation_id, "infra_id": infra_id,
                                    "stored_dns_label": infra.dns_label,
                                },
                            )
                    if "tls_status" in payload:
                        infra.tls_status = payload.get("tls_status")
                        update_fields.append("tls_status")
                    infra.save(update_fields=update_fields)

            self._retry_counts.pop(infra_id, None)
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as exc:
            transient = self._is_transient(exc)
            log.exception(
                "Error processing host_readiness event",
                extra={"correlation_id": correlation_id, "infra_id": infra_id},
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=transient)

    def start(self):
        self.consumer.start(self.callback)

    def stop(self):
        self.consumer.stop()

    def close(self):
        self.stop()


class InfraExitedEventConsumer:
    """Consume infrastructure.exited and latch it onto the read-model (H2 — see
    plan/H-hardening.md).

    Deliberately NOT gated by host_readiness_version: that counter orders repeated
    snapshots of a value that can move in either direction (dns_synced/https_ready flip on
    every terraform apply), so "higher wins" is the right rule for it. exited_at is a
    one-way latch — infrastructure-service's own field is set once by the exit flow and
    never cleared — so the only ordering rule that makes sense here is "first exited event
    wins, and nothing ever un-sets it", the same write-once pattern already used for
    dns_label. A stale or redelivered event (earlier exited_at, or a duplicate) is
    therefore harmless to re-apply: once the local field is set, every later event is a
    no-op, never a clear."""

    EXCHANGE_NAME = "infrastructure.events"
    ROUTING_KEY = "infrastructure.exited"
    QUEUE_NAME = "application-service.infra-exited-events"
    MAX_RETRIES = 10

    def __init__(self):
        self._retry_counts: dict = {}
        self.consumer = ResilientPikaConsumer(
            url=app_config.rabbitmq_url,
            exchange=self.EXCHANGE_NAME,
            queue=self.QUEUE_NAME,
            routing_key=self.ROUTING_KEY,
            name="application-service-infra-exited-consumer",
            prefetch_count=1,
        )

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        return isinstance(exc, (ObjectDoesNotExist, OperationalError))

    def callback(self, ch, method, properties, body):
        correlation_id = (
            properties.correlation_id
            if properties and properties.correlation_id
            else str(uuid.uuid4())
        )
        log = logger.getChild("infra_exited_event")

        try:
            event = json.loads(body)
        except json.JSONDecodeError:
            log.error("JSON decode failed — discarding", extra={"correlation_id": correlation_id})
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        payload = event.get("payload", {})
        infra_id = payload.get("infra_id")
        if not infra_id:
            log.warning("infra_exited event missing infra_id — discarding", extra={"correlation_id": correlation_id})
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        exited_at = payload.get("exited_at")
        if not exited_at:
            log.error(
                "infra_exited event missing exited_at — discarding",
                extra={"correlation_id": correlation_id, "infra_id": infra_id},
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        try:
            from api.models.infrastructure import Infrastructure
            from django.utils import timezone
            from django.utils.dateparse import parse_datetime

            connection.close()

            infra = Infrastructure.objects.filter(id=infra_id).first()
            if infra is None:
                retry_count = self._retry_counts.get(infra_id, 0)
                if retry_count >= self.MAX_RETRIES:
                    log.warning(
                        "infra_exited event unresolved after max retries — discarding (likely stale infra)",
                        extra={"correlation_id": correlation_id, "infra_id": infra_id},
                    )
                    self._retry_counts.pop(infra_id, None)
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                else:
                    self._retry_counts[infra_id] = retry_count + 1
                    delay = min(2 ** retry_count, 30)
                    log.warning(
                        "infra_exited event deferred — infra not synced yet (attempt %d/%d, delay %ds)",
                        retry_count + 1, self.MAX_RETRIES, delay,
                        extra={"correlation_id": correlation_id, "infra_id": infra_id},
                    )
                    # Security review RECOMMENDED 3: NOT time.sleep — this callback runs on
                    # a pika BlockingConnection's own I/O thread, and a bare time.sleep()
                    # starves that connection's event loop for the duration, so the broker
                    # never sees a heartbeat and can drop the connection under load.
                    # connection.sleep() blocks for the same delay but keeps pumping
                    # process_data_events internally, which is pika's own documented
                    # answer to exactly this. MAX_RETRIES (10, unchanged) is already the
                    # bounded "give up after N attempts" the alternative (a real AMQP DLQ)
                    # would also provide — no DLX exists anywhere in this codebase's AMQP
                    # consumers (the "DLQ" in inspect_dlq.py is a separate Redis structure
                    # for the deployment job queue, not this exchange), so adding one here
                    # is out of scope for this fix.
                    ch.connection.sleep(delay)
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                return

            with transaction.atomic():
                # Write-once latch, same pattern as dns_label: a stored value is never
                # overwritten, regardless of what the incoming timestamp says — this field
                # only ever gates checks (deploy/rollback/create refused once set), never
                # the reverse, so there is no such thing as a "fresher" exited_at worth
                # applying over an already-set one.
                if infra.exited_at is None:
                    parsed = parse_datetime(exited_at)
                    infra.exited_at = parsed or timezone.now()
                    infra.save(update_fields=["exited_at"])
                else:
                    log.info(
                        "infra_exited event received for an already-exited infra — no-op",
                        extra={"correlation_id": correlation_id, "infra_id": infra_id},
                    )

            self._retry_counts.pop(infra_id, None)
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as exc:
            transient = self._is_transient(exc)
            log.exception(
                "Error processing infra_exited event",
                extra={"correlation_id": correlation_id, "infra_id": infra_id},
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=transient)

    def start(self):
        self.consumer.start(self.callback)

    def stop(self):
        self.consumer.stop()

    def close(self):
        self.stop()


class InfraDeletedEventConsumer:
    """Consume infrastructure.deleted events and drop the local read-model row."""

    EXCHANGE_NAME = "infrastructure.events"
    ROUTING_KEY = "infrastructure.deleted"
    QUEUE_NAME = "application-service.infra-deleted-events"

    def __init__(self):
        self.infra_repo = InfrastructureRepository()
        self.consumer = ResilientPikaConsumer(
            url=app_config.rabbitmq_url,
            exchange=self.EXCHANGE_NAME,
            queue=self.QUEUE_NAME,
            routing_key=self.ROUTING_KEY,
            name="application-service-infra-deleted-consumer",
            prefetch_count=1,
        )

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        return isinstance(exc, (OperationalError,))

    def callback(self, ch, method, properties, body):
        correlation_id = (
            properties.correlation_id
            if properties and properties.correlation_id
            else str(uuid.uuid4())
        )
        log = logger.getChild("infra_deleted_event")

        try:
            event = json.loads(body)
        except json.JSONDecodeError:
            log.error("JSON decode failed — discarding", extra={"correlation_id": correlation_id})
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        payload = event.get("payload", {})
        infra_id = payload.get("id") or payload.get("infra_id")
        if not infra_id:
            log.warning("infra deleted event missing id — discarding", extra={"correlation_id": correlation_id})
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        try:
            connection.close()
            with transaction.atomic():
                removed = self.infra_repo.delete_infrastructure(infra_id)
            log.info(
                "infrastructure.deleted processed — ACKing",
                extra={"correlation_id": correlation_id, "infra_id": infra_id, "removed": removed},
            )
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as exc:
            transient = self._is_transient(exc)
            log.exception(
                "Error processing infrastructure.deleted event",
                extra={"correlation_id": correlation_id, "infra_id": infra_id},
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=transient)

    def start(self):
        self.consumer.start(self.callback)

    def stop(self):
        self.consumer.stop()

    def close(self):
        self.stop()


class InfraUserRemovedEventConsumer:
    """Consume infrastructure.user_removed and drop the member from the local read-model,
    so a user the owner removed from an infra loses application-level access here too."""

    EXCHANGE_NAME = "infrastructure.events"
    ROUTING_KEY = "infrastructure.user_removed"
    QUEUE_NAME = "application-service.infra-user-removed-events"

    def __init__(self):
        self.consumer = ResilientPikaConsumer(
            url=app_config.rabbitmq_url,
            exchange=self.EXCHANGE_NAME,
            queue=self.QUEUE_NAME,
            routing_key=self.ROUTING_KEY,
            name="application-service-infra-user-removed-consumer",
            prefetch_count=1,
        )

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        # ProgrammingError (schema mismatch) is permanent — only a lost connection is worth
        # requeueing. Requeueing a permanent error against a DLX-less queue loops forever.
        return isinstance(exc, OperationalError)

    def callback(self, ch, method, properties, body):
        correlation_id = (
            properties.correlation_id
            if properties and properties.correlation_id
            else str(uuid.uuid4())
        )
        log = logger.getChild("infra_user_removed_event")

        try:
            event = json.loads(body)
        except json.JSONDecodeError:
            log.error("JSON decode failed — discarding", extra={"correlation_id": correlation_id})
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        payload = event.get("payload", {})
        infra_id = payload.get("infra_id") or payload.get("id")
        user_id = payload.get("user_id")
        if not infra_id or not user_id:
            log.warning("infra user_removed event missing infra_id/user_id — discarding", extra={"correlation_id": correlation_id})
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        try:
            from api.models.infrastructure import Infrastructure
            from api.models.user import User
            connection.close()
            with transaction.atomic():
                infra = Infrastructure.objects.filter(id=infra_id).first()
                user = User.objects.filter(id=user_id).first()
                removed = bool(infra and user)
                if removed:
                    infra.invited_users.remove(user)
            log.info(
                "infrastructure.user_removed processed — ACKing",
                extra={"correlation_id": correlation_id, "infra_id": infra_id, "user_id": user_id, "removed": removed},
            )
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as exc:
            transient = self._is_transient(exc)
            log.exception(
                "Error processing infrastructure.user_removed event",
                extra={"correlation_id": correlation_id, "infra_id": infra_id, "user_id": user_id},
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=transient)

    def start(self):
        self.consumer.start(self.callback)

    def stop(self):
        self.consumer.stop()

    def close(self):
        self.stop()
