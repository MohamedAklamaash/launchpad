import json
import uuid
import logging
from django.db import transaction, connection, ProgrammingError, OperationalError
from django.core.exceptions import ObjectDoesNotExist
from api.repositories.infrastructure import InfrastructureRepository
from api.common.envs.application import app_config
from shared.resilience import ResilientPikaConsumer

logger = logging.getLogger(__name__)


class InfraEventConsumer:
    """Consume infrastructure.created events from RabbitMQ and sync local database."""

    EXCHANGE_NAME = "infrastructure.events"
    ROUTING_KEY = "infrastructure.created"
    QUEUE_NAME = "application-service.infra-events"

    def __init__(self):
        self.infra_repo = InfrastructureRepository()
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
        return isinstance(exc, (ObjectDoesNotExist, ProgrammingError, OperationalError))

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
            log.error(
                "JSON decode failed — discarding unparseable message",
                extra={"correlation_id": correlation_id, "error": str(exc)},
                exc_info=True,
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
                        "max_cpu": payload.get("max_cpu", 0),
                        "max_memory": payload.get("max_memory", 0),
                        "code": payload.get("code"),
                        "is_cloud_authenticated": payload.get("is_cloud_authenticated", False),
                        "metadata": payload.get("metadata"),
                    }
                )

            log.info(
                "infrastructure upserted successfully — ACKing",
                extra={"correlation_id": correlation_id, "infra_id": infra_id},
            )
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as exc:
            transient = self._is_transient(exc)
            log.error(
                "Error persisting infrastructure event — %s",
                "NACKing with requeue (transient)" if transient else "NACKing without requeue (permanent)",
                extra={
                    "correlation_id": correlation_id,
                    "infra_id": infra_id,
                    "user_id": user_id,
                    "error": str(exc),
                    "exc_type": type(exc).__name__,
                },
                exc_info=True,
            )
            ch.basic_nack(
                delivery_tag=method.delivery_tag,
                requeue=transient,
            )

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

    def __init__(self):
        self.infra_repo = InfrastructureRepository()
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
        return isinstance(exc, (ObjectDoesNotExist, ProgrammingError, OperationalError))

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
        except json.JSONDecodeError as exc:
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
            with transaction.atomic():
                infra = self.infra_repo.get_infrastructure(infra_id)
                if infra:
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
                else:
                    log.warning(f"Infrastructure {infra_id} not found")

            ch.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as exc:
            transient = self._is_transient(exc)
            log.error(
                "Error processing infrastructure.updated event",
                extra={"correlation_id": correlation_id, "infra_id": infra_id},
                exc_info=True,
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=transient)

    def start(self):
        self.consumer.start(self.callback)

    def stop(self):
        self.consumer.stop()

    def close(self):
        self.stop()
