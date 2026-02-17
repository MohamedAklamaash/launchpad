import json
import logging
import pika
from api.repositories.infrastructure import InfrastructureRepository
from api.common.env.application import app_config

logger = logging.getLogger(__name__)

class InfraEventConsumer:
    EXCHANGE_NAME = "infrastructure.events"
    ROUTING_KEY = "infrastructure.created"
    QUEUE_NAME = "payment-service.infra-events"

    def __init__(self):
        self.connection = None
        self.channel = None
        self.infra_repo = InfrastructureRepository()

    def connect(self):
        parameters = pika.URLParameters(app_config.rabbitmq_url)
        self.connection = pika.BlockingConnection(parameters)
        self.channel = self.connection.channel()
        
        self.channel.exchange_declare(
            exchange=self.EXCHANGE_NAME,
            exchange_type='topic',
            durable=True
        )
        
        self.channel.queue_declare(queue=self.QUEUE_NAME, durable=True)
        self.channel.queue_bind(
            exchange=self.EXCHANGE_NAME,
            queue=self.QUEUE_NAME,
            routing_key=self.ROUTING_KEY
        )

    def callback(self, ch, method, properties, body):
        try:
            event = json.loads(body)
            payload = event.get("payload", {})
            
            infra_id = payload.get("id") or payload.get("infra_id")
            user_id = payload.get("user_id")
            name = payload.get("name")
            status = payload.get("status", "active")

            if not infra_id or not user_id:
                logger.warning(f"Received invalid infra event payload (missing id or user_id): {payload}")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            self.infra_repo.upsert_infrastructure({
                "id": infra_id,
                "user_id": user_id,
                "name": name,
                "status": status,
            })
            
            logger.info(f"Successfully synced infra {infra_id} for user {user_id}")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            
        except Exception as e:
            logger.error(f"Error processing infra event: {e}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    def start(self):
        if not self.channel:
            self.connect()
            
        logger.info(f"Starting infra consumer on queue {self.QUEUE_NAME}")
        self.channel.basic_consume(
            queue=self.QUEUE_NAME,
            on_message_callback=self.callback
        )
        self.channel.start_consuming()

    def close(self):
        if self.connection and not self.connection.is_closed:
            self.connection.close()
