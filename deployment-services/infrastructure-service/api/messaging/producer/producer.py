import json
import logging
import pika
from api.common.envs.application import app_config

logger = logging.getLogger(__name__)

class InfraEventProducer:
    EXCHANGE_NAME = "infrastructure.events"
    ROUTING_KEY = "infrastructure.created"

    def __init__(self):
        self.connection = None
        self.channel = None

    def connect(self):
        try:
            parameters = pika.URLParameters(app_config.rabbitmq_url)
            self.connection = pika.BlockingConnection(parameters)
            self.channel = self.connection.channel()
            
            self.channel.exchange_declare(
                exchange=self.EXCHANGE_NAME,
                exchange_type='topic',
                durable=True
            )
            logger.info("InfraEventProducer connected to RabbitMQ")
        except Exception as e:
            logger.error(f"Failed to connect InfraEventProducer: {e}")
            raise e

    def publish_infra_created(self, user_id, infra_id):
        if not self.channel or self.connection.is_closed:
            self.connect()

        event = {
            "type": self.ROUTING_KEY,
            "payload": {
                "user_id": str(user_id),
                "infra_id": str(infra_id)
            },
            "occurredAt": None, # Could add timestamps if needed
            "metadata": {"version": 1}
        }

        try:
            self.channel.basic_publish(
                exchange=self.EXCHANGE_NAME,
                routing_key=self.ROUTING_KEY,
                body=json.dumps(event),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # make message persistent
                    content_type='application/json'
                )
            )
            logger.info(f"Published infra.created event for user {user_id}, infra {infra_id}")
        except Exception as e:
            logger.error(f"Failed to publish infra.created event: {e}")

    def close(self):
        if self.connection and not self.connection.is_closed:
            self.connection.close()

# Singleton instance
infra_producer = InfraEventProducer()
