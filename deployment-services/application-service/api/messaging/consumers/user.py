import json
import logging
import time
import uuid

from api.common.envs.application import app_config
from api.repositories.user import PendingInvitedInfrastructure, UserRepository
from django.db import OperationalError, connection
from shared.resilience import ResilientPikaConsumer

logger = logging.getLogger(__name__)


class AuthEventConsumer:
    """Consume auth events from RabbitMQ and sync local user database."""

    EXCHANGE_NAME = "auth.events"
    ROUTING_KEY = "auth.user.registered"
    QUEUE_NAME = "application-service.auth-events"
    # Invite-link is best-effort and re-attempted on the next auth event, so a short retry covers
    # same-session ordering without holding the message (and spamming logs) for minutes per login.
    MAX_RETRIES = 3

    def __init__(self):
        self.user_repo = UserRepository()
        self._retry_counts: dict = {}
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
        OperationalError → DB connection lost — retry.
        Everything else (incl. ProgrammingError, a permanent schema mismatch) → discard,
        so a poison message can't loop forever against a DLX-less queue.
        """
        return isinstance(exc, (OperationalError,))

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
            log.exception(
                "JSON decode failed — discarding unparseable message",
                extra={"correlation_id": correlation_id, "error": str(exc)},
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        payload = event.get("payload", {})

        user_id = payload.get("id")
        email = payload.get("email")
        user_name = payload.get("user_name")
        role = payload.get("role")
        roles = payload.get("roles", {})
        invited_by = payload.get("invited_by")
        metadata = payload.get("metadata", {})
        infra_id = payload.get("infra_id", [])

        log.info(
            "Received auth.user.registered event",
            extra={
                "correlation_id": correlation_id,
                "user_id": user_id,
                "email": email,
                "event_type": event.get("type"),
                "invited_by": invited_by,
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
            connection.close()
            self.user_repo.upsert_user(
                {
                    "id": user_id,
                    "email": email,
                    "user_name": user_name,
                    "role": role,
                    "roles": roles,
                    "is_active": True,
                    "metadata": metadata,
                    "invited_by": invited_by,
                    "infra_id": infra_id if isinstance(infra_id, list) else [infra_id] if infra_id else [],
                }
            )

            log.info(
                "user upserted successfully — ACKing",
                extra={"correlation_id": correlation_id, "user_id": user_id},
            )
            self._retry_counts.pop(user_id, None)
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except PendingInvitedInfrastructure as exc:
            # User is already committed; only the invite link is outstanding. Retry it (bounded)
            # until the infra materializes, then give up on the link rather than the whole user.
            retry_count = self._retry_counts.get(user_id, 0)
            if retry_count >= self.MAX_RETRIES:
                log.warning(
                    "Invited infrastructures still not materialized after max retries — "
                    "ACKing user without those links",
                    extra={"correlation_id": correlation_id, "user_id": user_id, "missing": exc.missing},
                )
                self._retry_counts.pop(user_id, None)
                ch.basic_ack(delivery_tag=method.delivery_tag)
            else:
                self._retry_counts[user_id] = retry_count + 1
                delay = min(2 ** retry_count, 5)
                log.info(
                    "Invited infrastructure not materialized yet — requeueing to retry link "
                    "(attempt %d/%d, delay %ds)",
                    retry_count + 1, self.MAX_RETRIES, delay,
                    extra={"correlation_id": correlation_id, "user_id": user_id, "missing": exc.missing},
                )
                time.sleep(delay)
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

        except Exception as exc:
            transient = self._is_transient(exc)
            log.exception(
                "Error processing auth event — %s",
                "NACKing with requeue (transient)" if transient else "NACKing without requeue (permanent)",
                extra={
                    "correlation_id": correlation_id,
                    "user_id": user_id,
                    "error": str(exc),
                    "exc_type": type(exc).__name__,
                },
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
