import json
import logging
import time
import uuid

from api.common.envs.application import app_config
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
