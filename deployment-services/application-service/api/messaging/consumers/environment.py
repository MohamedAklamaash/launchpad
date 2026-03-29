import json
import time
import uuid
import logging
from django.db import transaction, connection
from api.models import Environment, Infrastructure
from api.common.envs.application import app_config
from shared.resilience import ResilientPikaConsumer

logger = logging.getLogger(__name__)


class EnvironmentEventConsumer:
    """Consume environment.updated events from RabbitMQ and sync local database."""

    EXCHANGE_NAME = "infrastructure.events"
    ROUTING_KEY = "environment.updated"
    QUEUE_NAME = "application-service.environment-events"

    MAX_RETRIES = 10

    def __init__(self):
        self._retry_counts: dict = {}
        self.consumer = ResilientPikaConsumer(
            url=app_config.rabbitmq_url,
            exchange=self.EXCHANGE_NAME,
            queue=self.QUEUE_NAME,
            routing_key=self.ROUTING_KEY,
            name="application-service-environment-consumer",
            prefetch_count=1,
        )

    def callback(self, ch, method, properties, body):
        """Process received environment.updated events."""
        correlation_id = (
            properties.correlation_id
            if properties and properties.correlation_id
            else str(uuid.uuid4())
        )
        log = logger.getChild("environment_event")

        try:
            event = json.loads(body)
        except json.JSONDecodeError as exc:
            log.error(
                "JSON decode failed — discarding unparseable message",
                extra={"correlation_id": correlation_id, "error": str(exc)},
                exc_info=True,
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        payload = event.get("payload", {})
        env_id = payload.get("id") or payload.get("environment_id")
        infra_id = payload.get("infrastructure_id")

        log.info(
            "Received environment.updated event",
            extra={
                "correlation_id": correlation_id,
                "environment_id": env_id,
                "infrastructure_id": infra_id,
                "status": payload.get("status"),
            },
        )

        if not env_id or not infra_id:
            log.warning(
                "environment event missing required fields — discarding",
                extra={"correlation_id": correlation_id, "payload": payload},
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        try:
            connection.close()

            if not Infrastructure.objects.filter(id=infra_id).exists():
                retry_count = self._retry_counts.get(env_id, 0)

                if retry_count >= self.MAX_RETRIES:
                    log.error(
                        "Infrastructure still not found after max retries — discarding",
                        extra={"correlation_id": correlation_id, "infrastructure_id": infra_id, "retries": retry_count},
                    )
                    self._retry_counts.pop(env_id, None)
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                else:
                    self._retry_counts[env_id] = retry_count + 1
                    delay = min(2 ** retry_count, 30)
                    log.warning(
                        "Infrastructure not found yet — NACKing with requeue (attempt %d/%d, delay %ds)",
                        retry_count + 1, self.MAX_RETRIES, delay,
                        extra={"correlation_id": correlation_id, "infrastructure_id": infra_id},
                    )
                    time.sleep(delay)
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                return

            with transaction.atomic():
                env, created = Environment.objects.update_or_create(
                    id=env_id,
                    defaults={
                        "infrastructure_id": infra_id,
                        "status": payload.get("status", "PENDING"),
                        "vpc_id": payload.get("vpc_id"),
                        "cluster_arn": payload.get("cluster_arn"),
                        "alb_arn": payload.get("alb_arn"),
                        "alb_dns": payload.get("alb_dns"),
                        "alb_security_group_id": payload.get("alb_security_group_id"),
                        "target_group_arn": payload.get("target_group_arn"),
                        "ecr_repository_url": payload.get("ecr_repository_url"),
                        "ecs_task_execution_role_arn": payload.get("ecs_task_execution_role_arn"),
                    }
                )

            log.info(
                f"Environment {'created' if created else 'updated'} successfully — ACKing",
                extra={"correlation_id": correlation_id, "environment_id": env_id},
            )
            self._retry_counts.pop(env_id, None)
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as exc:
            log.error(
                "Error persisting environment event — NACKing with requeue",
                extra={
                    "correlation_id": correlation_id,
                    "environment_id": env_id,
                    "infrastructure_id": infra_id,
                    "error": str(exc),
                },
                exc_info=True,
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

    def start(self):
        """Start consuming messages."""
        self.consumer.start(self.callback)

    def stop(self):
        """Stop consuming messages."""
        self.consumer.stop()

    def close(self):
        """Close connection."""
        self.stop()
