import logging
import uuid
from datetime import datetime, timezone
from api.common.envs.application import app_config
from shared.resilience import ResilientPikaProducer

logger = logging.getLogger(__name__)


class InfraEventProducer:
    EXCHANGE_NAME = "infrastructure.events"
    ROUTING_KEY_INFRA_CREATED = "infrastructure.created"
    ROUTING_KEY_INFRA_UPDATED = "infrastructure.updated"
    ROUTING_KEY_ENV_UPDATED = "environment.updated"

    def __init__(self):
        self.producer = ResilientPikaProducer(
            url=app_config.rabbitmq_url,
            exchange=self.EXCHANGE_NAME,
            name="infra-service-producer",
        )

    def connect(self):
        try:
            self.producer.connect()
            logger.info("InfraEventProducer connected to RabbitMQ")
        except Exception as e:
            logger.error(f"Failed to connect InfraEventProducer: {e}", exc_info=True)
            raise

    def publish_infra_created(
        self,
        user_id,
        infra_id,
        name=None,
        cloud_provider=None,
        max_cpu=0,
        max_memory=0,
        code=None,
        is_cloud_authenticated=False,
        invited_users=None,
        metadata=None,
        correlation_id=None,
    ):
        """
        Publish an infrastructure.created event.

        The payload includes BOTH `id` and `infra_id` for cross-service
        compatibility (Python consumers use `id`, TypeScript consumers
        type-check `infra_id`).

        Must be called from inside transaction.on_commit() so the DB row
        is guaranteed to exist before any consumer tries to read it.
        """
        cid = correlation_id or str(uuid.uuid4())
        event = {
            "type": self.ROUTING_KEY_INFRA_CREATED,
            "payload": {
                "id": str(infra_id),
                "infra_id": str(infra_id),
                "user_id": str(user_id),
                "name": name,
                "cloud_provider": cloud_provider,
                "max_cpu": max_cpu,
                "max_memory": max_memory,
                "code": code,
                "is_cloud_authenticated": is_cloud_authenticated,
                "invited_users": invited_users or [],
                "metadata": metadata or {},
                "status": "active",
            },
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {"version": 1, "correlation_id": cid},
        }

        logger.info(
            "Publishing infrastructure.created event",
            extra={
                "correlation_id": cid,
                "infra_id": str(infra_id),
                "user_id": str(user_id),
                "routing_key": self.ROUTING_KEY_INFRA_CREATED,
                "cloud_provider": cloud_provider,
            },
        )
        self.producer.publish(routing_key=self.ROUTING_KEY_INFRA_CREATED, body=event)
        logger.info(
            "Published infrastructure.created event",
            extra={"correlation_id": cid, "infra_id": str(infra_id)},
        )
    
    def publish_infrastructure_updated(
        self,
        user_id,
        infra_id,
        name=None,
        max_cpu=0,
        max_memory=0,
        correlation_id=None,
    ):
        """Publish infrastructure.updated event."""
        cid = correlation_id or str(uuid.uuid4())
        event = {
            "type": self.ROUTING_KEY_INFRA_UPDATED,
            "payload": {
                "id": str(infra_id),
                "infra_id": str(infra_id),
                "user_id": str(user_id),
                "name": name,
                "max_cpu": max_cpu,
                "max_memory": max_memory,
            },
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {"version": 1, "correlation_id": cid},
        }

        logger.info(
            "Publishing infrastructure.updated event",
            extra={
                "correlation_id": cid,
                "infra_id": str(infra_id),
                "user_id": str(user_id),
            },
        )
        self.producer.publish(routing_key=self.ROUTING_KEY_INFRA_UPDATED, body=event)
        logger.info(
            "Published infrastructure.updated event",
            extra={"correlation_id": cid, "infra_id": str(infra_id)},
        )
    
    def publish_environment_updated(
        self,
        infra_id,
        environment_id,
        status,
        vpc_id=None,
        cluster_arn=None,
        alb_arn=None,
        alb_dns=None,
        target_group_arn=None,
        ecr_repository_url=None,
        ecs_task_execution_role_arn=None,
        correlation_id=None,
    ):
        """Publish environment.updated event."""
        cid = correlation_id or str(uuid.uuid4())
        event = {
            "type": self.ROUTING_KEY_ENV_UPDATED,
            "payload": {
                "id": str(environment_id),
                "environment_id": str(environment_id),
                "infrastructure_id": str(infra_id),
                "status": status,
                "vpc_id": vpc_id,
                "cluster_arn": cluster_arn,
                "alb_arn": alb_arn,
                "alb_dns": alb_dns,
                "target_group_arn": target_group_arn,
                "ecr_repository_url": ecr_repository_url,
                "ecs_task_execution_role_arn": ecs_task_execution_role_arn,
            },
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {"version": 1, "correlation_id": cid},
        }

        logger.info(
            "Publishing environment.updated event",
            extra={
                "correlation_id": cid,
                "environment_id": str(environment_id),
                "infra_id": str(infra_id),
                "status": status,
            },
        )
        self.producer.publish(routing_key=self.ROUTING_KEY_ENV_UPDATED, body=event)

    def close(self):
        self.producer.close()


infra_producer = InfraEventProducer()
