import json
import logging
from api.common.envs.application import app_config
from shared.resilience import ResilientPikaProducer

logger = logging.getLogger(__name__)

_producer = ResilientPikaProducer(
    url=app_config.rabbitmq_url,
    exchange="application_events",
    name="application-service-producer",
)


class ApplicationEventProducer:
    @staticmethod
    def _publish(routing_key: str, body: dict):
        try:
            _producer.publish(routing_key=routing_key, body=json.dumps(body))
            logger.info(f"Published {routing_key}")
        except Exception as e:
            logger.error(f"Failed to publish {routing_key}: {e}")

    @staticmethod
    def publish_application_created(app_id, infrastructure_id, name, user_id):
        ApplicationEventProducer._publish("application.created", {
            "id": str(app_id),
            "infrastructure_id": str(infrastructure_id),
            "name": name,
            "user_id": str(user_id),
        })

    @staticmethod
    def publish_application_updated(app):
        ApplicationEventProducer._publish("application.updated", {
            "id": str(app.id),
            "name": app.name,
            "infrastructure_id": str(app.infrastructure_id),
            "alloted_cpu": app.alloted_cpu,
            "alloted_memory": app.alloted_memory,
            "port": app.port,
            "project_branch": app.project_branch,
            "dockerfile_path": app.dockerfile_path,
            "envs": app.envs,
        })

    @staticmethod
    def publish_application_deleted(app_id):
        ApplicationEventProducer._publish("application.deleted", {"id": str(app_id)})
