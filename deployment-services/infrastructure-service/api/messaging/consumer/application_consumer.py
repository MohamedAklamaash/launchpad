import json
import logging
import threading
import pika
from api.models import Application

logger = logging.getLogger(__name__)

class ApplicationEventConsumer:
    def __init__(self, rabbitmq_url):
        self.rabbitmq_url = rabbitmq_url
        self.connection = None
        self.channel = None
    
    def connect(self):
        self.connection = pika.BlockingConnection(pika.URLParameters(self.rabbitmq_url))
        self.channel = self.connection.channel()
        self.channel.exchange_declare(exchange='application_events', exchange_type='topic', durable=True)
        queue_name = 'infrastructure-service.application-events'
        self.channel.queue_declare(queue=queue_name, durable=True)
        self.channel.queue_bind(exchange='application_events', queue=queue_name, routing_key='application.created')
        self.channel.queue_bind(exchange='application_events', queue=queue_name, routing_key='application.deleted')
        self.channel.basic_qos(prefetch_count=1)
        self.channel.basic_consume(queue=queue_name, on_message_callback=self.callback, auto_ack=True)
    
    def callback(self, ch, method, properties, body):
        try:
            data = json.loads(body)
            routing_key = method.routing_key
            
            if routing_key == 'application.created':
                Application.objects.update_or_create(
                    id=data['id'],
                    defaults={
                        'infrastructure_id': data['infrastructure_id'],
                        'name': data['name'],
                        'user_id': data['user_id']
                    }
                )
                logger.info(f"Synced application created: {data['id']}")
            
            elif routing_key == 'application.deleted':
                Application.objects.filter(id=data['id']).delete()
                logger.info(f"Synced application deleted: {data['id']}")
        
        except Exception as e:
            logger.error(f"Error processing application event: {e}")
    
    def start(self):
        try:
            self.connect()
            logger.info("Application event consumer started")
            self.channel.start_consuming()
        except Exception as e:
            logger.error(f"Application event consumer error: {e}")
    
    def stop(self):
        if self.channel:
            self.channel.stop_consuming()
        if self.connection:
            self.connection.close()

def start_application_event_consumer(rabbitmq_url):
    consumer = ApplicationEventConsumer(rabbitmq_url)
    thread = threading.Thread(target=consumer.start, daemon=True)
    thread.start()
    return consumer
