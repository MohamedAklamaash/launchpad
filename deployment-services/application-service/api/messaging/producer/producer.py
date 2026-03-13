import json
import logging
import pika
from api.common.envs.application import app_config

logger = logging.getLogger(__name__)

class ApplicationEventProducer:
    @staticmethod
    def publish_application_created(app_id, infrastructure_id, name, user_id):
        try:
            connection = pika.BlockingConnection(pika.URLParameters(app_config.rabbitmq_url))
            channel = connection.channel()
            channel.exchange_declare(exchange='application_events', exchange_type='topic', durable=True)
            
            message = {
                'id': str(app_id),
                'infrastructure_id': str(infrastructure_id),
                'name': name,
                'user_id': str(user_id)
            }
            
            channel.basic_publish(
                exchange='application_events',
                routing_key='application.created',
                body=json.dumps(message)
            )
            connection.close()
            logger.info(f"Published application.created event for {app_id}")
        except Exception as e:
            logger.error(f"Failed to publish application.created: {e}")
    
    @staticmethod
    def publish_application_deleted(app_id):
        try:
            connection = pika.BlockingConnection(pika.URLParameters(app_config.rabbitmq_url))
            channel = connection.channel()
            channel.exchange_declare(exchange='application_events', exchange_type='topic', durable=True)
            
            message = {'id': str(app_id)}
            
            channel.basic_publish(
                exchange='application_events',
                routing_key='application.deleted',
                body=json.dumps(message)
            )
            connection.close()
            logger.info(f"Published application.deleted event for {app_id}")
        except Exception as e:
            logger.error(f"Failed to publish application.deleted: {e}")
