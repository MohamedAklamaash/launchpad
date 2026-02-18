import logging
import json
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
        try:
            event = json.loads(body)
            payload = event.get("payload", {})
            
            user_id = payload.get("id")
            email = payload.get("email")
            user_name = payload.get("user_name")
            role = payload.get("role")
            metadata = payload.get("metadata", {})
            infra_ids = payload.get("infra_id", [])

            if not user_id or not email:
                logger.warning(f"Received invalid event payload: {payload}")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            user, created = self.user_repo.upsert_user({
                "id": user_id,
                "email": email,
                "user_name": user_name,
                "role": role,
                "is_active": True,
                "is_staff": True,
                "metadata": metadata,
            })
            
            if infra_ids:
                for infra_id in infra_ids:
                    try:
                        infra = Infrastructure.objects.get(id=infra_id)
                        infra.invited_users.add(user)
                    except Infrastructure.DoesNotExist:
                        logger.warning(f"Infra {infra_id} not found when syncing user {user_id}")
            
            logger.info(f"Successfully synced user {email} ({user_id}) and linked to {len(infra_ids)} infrastructures")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            
        except Exception as e:
            logger.error(f"Error processing auth event: {e}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    def start(self):
        self.consumer.start(self.callback)

    def stop(self):
        self.consumer.stop()

    def close(self):
        self.stop()
