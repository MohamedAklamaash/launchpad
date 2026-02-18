import json
import logging
import pika
from api.repositories.infrastructure import InfrastructureRepository
from api.common.envs.application import app_config

logger = logging.getLogger(__name__)

class InfraEventConsumer:
    """Consume infrastructure events from RabbitMQ and sync local database."""
    EXCHANGE_NAME = "infrastructure.events"
    ROUTING_KEY = "infrastructure.created"
    QUEUE_NAME = "application-service.infra-events"

    def __init__(self):
        self.connection = None
        self.channel = None
        self.infra_repo = InfrastructureRepository()

    def connect(self):
        """Establish connection to RabbitMQ and declare exchanges/queues."""
        parameters = pika.URLParameters(app_config.rabbitmq_url)
        self.connection = pika.BlockingConnection(parameters)
        self.channel = self.connection.channel()
        
        self.channel.exchange_declare(exchange=self.EXCHANGE_NAME, exchange_type='topic', durable=True)
        self.channel.queue_declare(queue=self.QUEUE_NAME, durable=True)
        self.channel.queue_bind(exchange=self.EXCHANGE_NAME, queue=self.QUEUE_NAME, routing_key=self.ROUTING_KEY)

    def callback(self, ch, method, properties, body):
        """Process received infrastructure events."""
        try:
            event = json.loads(body)
            payload = event.get("payload", {})
            infra_id = payload.get("id") or payload.get("infra_id")
            user_id = payload.get("user_id")

            if not infra_id or not user_id:
                logger.warning(f"Invalid infra event: {payload}")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            self.infra_repo.upsert_infrastructure({
                "id": infra_id,
                "user_id": user_id,
                "name": payload.get("name"),
                "cloud_provider": payload.get("cloud_provider"),
                "max_cpu": payload.get("max_cpu", 0),
                "max_memory": payload.get("max_memory", 0),
            })
            
            logger.info(f"Synced infra {infra_id}")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            
        except Exception as e:
            logger.error(f"Error processing infra event: {e}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    def start(self):
        """Start consuming messages."""
        if not self.channel: self.connect()
        logger.info(f"Starting infra consumer on {self.QUEUE_NAME}")
        self.channel.basic_consume(queue=self.QUEUE_NAME, on_message_callback=self.callback)
        self.channel.start_consuming()

    def close(self):
        """Close RabbitMQ connection."""
        if self.connection and not self.connection.is_closed:
            self.connection.close()
