import json
import uuid
import logging
from django.db import transaction, connection, ProgrammingError, OperationalError
from api.repositories.infrastructure import InfrastructureRepository
from api.common.env.application import app_config
from shared.resilience import ResilientPikaConsumer

logger = logging.getLogger(__name__)


class InfraEventConsumer:
    EXCHANGE_NAME = "infrastructure.events"
    ROUTING_KEY = "infrastructure.created"
    QUEUE_NAME = "payment-service.infra-events"

    def __init__(self):
        self.infra_repo = InfrastructureRepository()
        self.consumer = ResilientPikaConsumer(
            url=app_config.rabbitmq_url,
            exchange=self.EXCHANGE_NAME,
            queue=self.QUEUE_NAME,
            routing_key=self.ROUTING_KEY,
            name="payment-service-infra-consumer",
            prefetch_count=1,
        )

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        return isinstance(exc, (ProgrammingError, OperationalError))

    def callback(self, ch, method, properties, body):
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
                "JSON decode failed — discarding",
                extra={"correlation_id": correlation_id, "error": str(exc)},
                exc_info=True,
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        payload = event.get("payload", {})
        infra_id = payload.get("id") or payload.get("infra_id")
        user_id = payload.get("user_id")
        name = payload.get("name")
        status = payload.get("status", "active")

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
                    {"id": infra_id, "user_id": user_id, "name": name, "status": status}
                )
            log.info(
                "infrastructure upserted successfully — ACKing",
                extra={"correlation_id": correlation_id, "infra_id": infra_id},
            )
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as exc:
            transient = self._is_transient(exc)
            log.error(
                "Error processing infra event — %s",
                "NACKing with requeue (transient)" if transient else "NACKing without requeue (permanent)",
                extra={
                    "correlation_id": correlation_id,
                    "infra_id": infra_id,
                    "error": str(exc),
                    "exc_type": type(exc).__name__,
                },
                exc_info=True,
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=transient)

    def start(self):
        self.consumer.start(self.callback)

    def stop(self):
        self.consumer.stop()

    def close(self):
        self.stop()
