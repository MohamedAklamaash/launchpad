import json
import logging
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
            name="application-service-auth-consumer"
        )

    def callback(self, ch, method, properties, body):
        """Process received auth events."""
        try:
            event = json.loads(body)
            payload = event.get("payload", {})
            user_id = payload.get("id")
            email = payload.get("email")

            if not user_id or not email:
                logger.warning(f"Invalid auth event: {payload}")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            self.user_repo.upsert_user({
                "id": user_id,
                "email": email,
                "user_name": payload.get("user_name"),
                "role": payload.get("role"),
                "is_active": True,
            })
            
            logger.info(f"Synced user {user_id}")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            
        except Exception as e:
            logger.error(f"Error processing auth event: {e}")
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
