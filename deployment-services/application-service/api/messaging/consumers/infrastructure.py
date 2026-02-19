import json
import uuid
import logging
from django.db import transaction, ProgrammingError, OperationalError
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

        Transient errors:
          - User.DoesNotExist / ObjectDoesNotExist:  auth event hasn't arrived yet
          - ProgrammingError:  table doesn't exist yet (migrations not applied)
          - OperationalError:  DB connection dropped

        Permanent errors (NACK with requeue=False):
          - JSON decode failure
          - Missing required fields (caught before this point)
          - IntegrityError on a fully valid payload (duplicate PK etc.)
        """
        return isinstance(exc, (ObjectDoesNotExist, ProgrammingError, OperationalError))

    def callback(self, ch, method, properties, body):
        """
        Process received infrastructure.created events.

        ACK  → DB write committed successfully.
        NACK requeue=True  → transient error (user lag, DB down, missing table).
        NACK requeue=False → permanent error (bad JSON, missing required fields).
        """
        correlation_id = (
            properties.correlation_id
            if properties and properties.correlation_id
            else str(uuid.uuid4())
        )
        log = logger.getChild("infra_event")

        # ── 1. Deserialize ────────────────────────────────────────────────────
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

        # ── 2. Validate required fields ───────────────────────────────────────
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

        # ── 3. DB write inside atomic transaction ─────────────────────────────
        try:
            with transaction.atomic():
                self.infra_repo.upsert_infrastructure(
                    {
                        "id": infra_id,
                        "user_id": user_id,
                        "name": payload.get("name") or "",
                        "cloud_provider": payload.get("cloud_provider") or "",
                        "max_cpu": payload.get("max_cpu", 0),
                        "max_memory": payload.get("max_memory", 0),
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
