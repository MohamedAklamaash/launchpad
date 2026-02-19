import logging
import json
from django.db import transaction
from api.models.infrastructure import Infrastructure
from api.repositories.user import UserRepository
from api.common.envs.application import app_config
from shared.resilience import ResilientPikaConsumer

logger = logging.getLogger(__name__)


class AuthEventConsumer:
    EXCHANGE_NAME = "auth.events"
    ROUTING_KEY = "auth.user.registered"
    QUEUE_NAME = "infrastructure-service.auth-events"

    def __init__(self):
        self.user_repo = UserRepository()
        self.consumer = ResilientPikaConsumer(
            url=app_config.rabbitmq_url,
            exchange=self.EXCHANGE_NAME,
            queue=self.QUEUE_NAME,
            routing_key=self.ROUTING_KEY,
            name="infra-service-auth-consumer"
        )

    def callback(self, ch, method, properties, body):
        """
        Process auth.user.registered events.

        Guarantees:
        - ACK only after successful DB write commits.
        - NACK with requeue=False for invalid messages.
        - NACK with requeue=True for transient DB errors.
        - All DB operations wrapped in transaction.atomic().
        - No silent exceptions — every error is logged with exc_info=True.
        """
        try:
            event = json.loads(body)
        except json.JSONDecodeError as exc:
            logger.error(
                "AuthEventConsumer: JSON decode failed — discarding message",
                extra={"error": str(exc)},
                exc_info=True,
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        payload = event.get("payload", {})

        user_id = payload.get("id")
        email = payload.get("email")
        user_name = payload.get("user_name")
        role = payload.get("role")
        invited_by = payload.get("invited_by")
        metadata = payload.get("metadata", {})
        infra_ids = payload.get("infra_id", [])

        if not user_id or not email:
            logger.warning(
                "AuthEventConsumer: received event with missing user_id/email — discarding",
                extra={"payload": payload},
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        try:
            with transaction.atomic():
                user, created = self.user_repo.upsert_user({
                    "id": user_id,
                    "email": email,
                    "user_name": user_name,
                    "role": role,
                    "is_active": True,
                    "is_staff": True,
                    "metadata": metadata,
                    "invited_by": invited_by,
                })

                if infra_ids:
                    for infra_id in infra_ids:
                        try:
                            infra = Infrastructure.objects.get(id=infra_id)
                            infra.invited_users.add(user)
                        except Infrastructure.DoesNotExist:
                            logger.warning(
                                "AuthEventConsumer: infrastructure not found when linking user",
                                extra={"infra_id": infra_id, "user_id": user_id},
                            )

            logger.info(
                "AuthEventConsumer: user synced and linked to infrastructures",
                extra={
                    "user_id": user_id,
                    "email": email,
                    "infra_count": len(infra_ids),
                    "created": created,
                },
            )
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as exc:
            logger.error(
                "AuthEventConsumer: error processing auth event — NACKing with requeue",
                extra={"user_id": user_id, "email": email, "error": str(exc)},
                exc_info=True,
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

    def start(self):
        self.consumer.start(self.callback)

    def stop(self):
        self.consumer.stop()

    def close(self):
        self.stop()
