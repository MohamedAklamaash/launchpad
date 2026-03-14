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
    def publish_application_updated(app):
        try:
            connection = pika.BlockingConnection(pika.URLParameters(app_config.rabbitmq_url))
            channel = connection.channel()
            channel.exchange_declare(exchange='application_events', exchange_type='topic', durable=True)
            message = {
                'id': str(app.id),
                'name': app.name,
                'description': app.description,
                'infrastructure_id': str(app.infrastructure_id),
                'alloted_cpu': app.alloted_cpu,
                'alloted_memory': app.alloted_memory,
                'port': app.port,
                'project_branch': app.project_branch,
                'dockerfile_path': app.dockerfile_path,
                'envs': app.envs,
            }
            channel.basic_publish(exchange='application_events', routing_key='application.updated', body=json.dumps(message))
            connection.close()
            logger.info(f"Published application.updated event for {app.id}")
        except Exception as e:
            logger.error(f"Failed to publish application.updated: {e}")

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
