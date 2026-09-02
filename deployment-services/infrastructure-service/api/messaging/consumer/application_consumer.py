import json
import logging
import threading
import time

import pika
from api.models import Application
from django.db import connection

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
        self.channel.queue_bind(exchange='application_events', queue=queue_name, routing_key='application.updated')
        self.channel.queue_bind(exchange='application_events', queue=queue_name, routing_key='application.deleted')
        self.channel.basic_qos(prefetch_count=1)
        # Manual ack: auto_ack acknowledges before the handler runs, so a DB blip silently drops
        # the event and the read-model drifts permanently. Ack only after the write succeeds.
        self.channel.basic_consume(queue=queue_name, on_message_callback=self.callback, auto_ack=False)

    def callback(self, ch, method, properties, body):
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            logger.error("Application event JSON decode failed — discarding")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        routing_key = method.routing_key
        try:
            connection.close()
            if routing_key == 'application.created':
                Application.objects.update_or_create(
                    id=data['id'],
                    defaults={
                        'infrastructure_id': data['infrastructure_id'],
                        'name': data['name'],
                        'user_id': data['user_id'],
                    },
                )
                logger.info(f"Synced application created: {data['id']}")
            elif routing_key == 'application.updated':
                Application.objects.filter(id=data['id']).update(
                    name=data['name'],
                    infrastructure_id=data['infrastructure_id'],
                )
                logger.info(f"Synced application updated: {data['id']}")
            elif routing_key == 'application.deleted':
                Application.objects.filter(id=data['id']).delete()
                logger.info(f"Synced application deleted: {data['id']}")
            ch.basic_ack(delivery_tag=method.delivery_tag)
        except KeyError as e:
            logger.error(f"Malformed application event ({routing_key}) — discarding: {e}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        except Exception:
            logger.exception(f"Error processing application event ({routing_key}) — requeueing")
            time.sleep(1)
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
    
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
