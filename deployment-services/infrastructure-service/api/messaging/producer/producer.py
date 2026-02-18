import logging
from api.common.envs.application import app_config
from shared.resilience import ResilientPikaProducer

logger = logging.getLogger(__name__)

class InfraEventProducer:
    EXCHANGE_NAME = "infrastructure.events"
    ROUTING_KEY = "infrastructure.created"

    def __init__(self):
        self.producer = ResilientPikaProducer(
            url=app_config.rabbitmq_url,
            exchange=self.EXCHANGE_NAME,
            name="infra-service-producer"
        )

    def connect(self):
        try:
            self.producer.connect()
        except Exception as e:
            logger.error(f"Failed to connect InfraEventProducer: {e}")
            raise e

    def publish_infra_created(self, user_id, infra_id, name=None):
        event = {
            "type": self.ROUTING_KEY,
            "payload": {
                "user_id": str(user_id),
                "id": str(infra_id),
                "name": name,
                "status": "active"
            },
            "occured_at": None,
            "metadata": {"version": 1}
        }

        self.producer.publish(
            routing_key=self.ROUTING_KEY,
            body=event
        )
        logger.info(f"Published infra.created event for user {user_id}, infra {infra_id}")

    def close(self):
        self.producer.close()

infra_producer = InfraEventProducer()
