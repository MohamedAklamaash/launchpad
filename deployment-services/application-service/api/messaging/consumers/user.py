import json
import logging
import pika
from api.repositories.user import UserRepository
from api.common.envs.application import app_config

logger = logging.getLogger(__name__)

class AuthEventConsumer:
    """Consume auth events from RabbitMQ and sync local user database."""
    EXCHANGE_NAME = "auth.events"
    ROUTING_KEY = "auth.user.registered"
    QUEUE_NAME = "application-service.auth-events"

    def __init__(self):
        self.connection = None
        self.channel = None
        self.user_repo = UserRepository()

    def connect(self):
        """Establish connection to RabbitMQ."""
        parameters = pika.URLParameters(app_config.rabbitmq_url)
        self.connection = pika.BlockingConnection(parameters)
        self.channel = self.connection.channel()
        
        self.channel.exchange_declare(exchange=self.EXCHANGE_NAME, exchange_type='topic', durable=True)
        self.channel.queue_declare(queue=self.QUEUE_NAME, durable=True)
        self.channel.queue_bind(exchange=self.EXCHANGE_NAME, queue=self.QUEUE_NAME, routing_key=self.ROUTING_KEY)

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
        if not self.channel: self.connect()
        logger.info(f"Starting auth consumer on {self.QUEUE_NAME}")
        self.channel.basic_consume(queue=self.QUEUE_NAME, on_message_callback=self.callback)
        self.channel.start_consuming()

    def close(self):
        """Close connection."""
        if self.connection and not self.connection.is_closed:
            self.connection.close()
