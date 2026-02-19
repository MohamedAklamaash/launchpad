import logging
import uuid
from datetime import datetime, timezone
from api.common.envs.application import app_config
from shared.resilience import ResilientPikaProducer

logger = logging.getLogger(__name__)


class InfraEventProducer:
    EXCHANGE_NAME = "infrastructure.events"
    ROUTING_KEY = "infrastructure.created"

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
            "type": self.ROUTING_KEY,
            "payload": {
                "id": str(infra_id),           # canonical key for Python consumers
                "infra_id": str(infra_id),     # compat key for TypeScript consumers
                "user_id": str(user_id),
                "name": name,
                "cloud_provider": cloud_provider,
                "max_cpu": max_cpu,
                "max_memory": max_memory,
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
                "routing_key": self.ROUTING_KEY,
                "cloud_provider": cloud_provider,
            },
        )
        self.producer.publish(routing_key=self.ROUTING_KEY, body=event)
        logger.info(
            "Published infrastructure.created event",
            extra={"correlation_id": cid, "infra_id": str(infra_id)},
        )

    def close(self):
        self.producer.close()


infra_producer = InfraEventProducer()
