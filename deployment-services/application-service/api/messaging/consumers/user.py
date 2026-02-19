import json
import uuid
import logging
from django.db import transaction, ProgrammingError, OperationalError
from api.repositories.user import UserRepository
from api.common.envs.application import app_config
from shared.resilience import ResilientPikaConsumer

logger = logging.getLogger(__name__)


class AuthEventConsumer:
    """Consume auth events from RabbitMQ and sync local user database."""

    EXCHANGE_NAME = "auth.events"
    ROUTING_KEY = "auth.user.registered"
    QUEUE_NAME = "application-service.auth-events"

    def __init__(self):
        self.user_repo = UserRepository()
        self.consumer = ResilientPikaConsumer(
            url=app_config.rabbitmq_url,
            exchange=self.EXCHANGE_NAME,
            queue=self.QUEUE_NAME,
            routing_key=self.ROUTING_KEY,
            name="application-service-auth-consumer",
            prefetch_count=1,
        )

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        """
        ProgrammingError  → table doesn't exist yet (unapplied migrations) — retry
        OperationalError  → DB connection lost — retry
        Everything else   → permanent, discard
        """
        return isinstance(exc, (ProgrammingError, OperationalError))

    def callback(self, ch, method, properties, body):
        """Process received auth.user.registered events."""
        correlation_id = (
            properties.correlation_id
            if properties and properties.correlation_id
            else str(uuid.uuid4())
        )
        log = logger.getChild("auth_event")

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

        user_id = payload.get("id")
        email = payload.get("email")
        user_name = payload.get("user_name")
        role = payload.get("role")
        metadata = payload.get("metadata", {})

        log.info(
            "Received auth.user.registered event",
            extra={
                "correlation_id": correlation_id,
                "user_id": user_id,
                "email": email,
                "event_type": event.get("type"),
            },
        )

        if not user_id or not email:
            log.warning(
                "auth event missing required fields user_id/email — discarding",
                extra={"correlation_id": correlation_id, "payload": payload},
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        try:
            with transaction.atomic():
                self.user_repo.upsert_user(
                    {
                        "id": user_id,
                        "email": email,
                        "user_name": user_name,
                        "role": role,
                        "is_active": True,
                        "metadata": metadata,
                    }
                )

            log.info(
                "user upserted successfully — ACKing",
                extra={"correlation_id": correlation_id, "user_id": user_id},
            )
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as exc:
            transient = self._is_transient(exc)
            log.error(
                "Error processing auth event — %s",
                "NACKing with requeue (transient)" if transient else "NACKing without requeue (permanent)",
                extra={
                    "correlation_id": correlation_id,
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
