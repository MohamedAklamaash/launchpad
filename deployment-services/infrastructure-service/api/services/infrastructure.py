import os
import uuid
import logging
from django.db import transaction
from api.repositories.infrastructure import InfrastructureRepository
from api.serializers.infrastructure import InfrastructureSerializer
from shared.resilience.circuit_breaker import CircuitBreaker
from api.messaging.producer.producer import infra_producer

logger = logging.getLogger(__name__)

cloud_cb = CircuitBreaker(
    name="CloudProviderAPI",
    failure_threshold=int(os.environ.get("CB_FAILURE_THRESHOLD", 5)),
    timeout=float(os.environ.get("CB_TIMEOUT_MS", 30000)) / 1000.0,
    success_threshold=int(os.environ.get("CB_SUCCESS_THRESHOLD", 2))
)


class InfrastructureService:
    def __init__(self):
        self.repo = InfrastructureRepository()

    def get_all_for_user(self, user_id):
        infras = self.repo.get_all_for_user(user_id)
        return InfrastructureSerializer.serialize_list(infras)

    def get_infrastructure(self, user_id, infra_id):
        infra = self.repo.get_by_id(user_id, infra_id)
        if infra:
            return InfrastructureSerializer.serialize_instance(infra)
        return None

    def create_infrastructure(self, user_id, infra_data):
        """
        Create an Infrastructure row and publish the InfrastructureCreated event.
        """
        correlation_id = str(uuid.uuid4())

        infra = self.repo.create(user_id, infra_data)
        serialized_infra = InfrastructureSerializer.serialize_instance(infra)

        infra_id = serialized_infra["id"]
        cloud_provider = serialized_infra.get("cloud_provider")
        max_cpu = serialized_infra.get("max_cpu", 0)
        max_memory = serialized_infra.get("max_memory", 0)
        invited_users = serialized_infra.get("invited_users", [])
        metadata = serialized_infra.get("metadata") or {}

        def _publish():
            try:
                infra_producer.publish_infra_created(
                    user_id=user_id,
                    infra_id=infra_id,
                    name=serialized_infra.get("name"),
                    cloud_provider=cloud_provider,
                    max_cpu=max_cpu,
                    max_memory=max_memory,
                    invited_users=[str(uid) for uid in invited_users],
                    metadata=metadata,
                    correlation_id=correlation_id,
                )
            except Exception as e:
                logger.error(
                    "Failed to publish infra_created event after DB commit",
                    extra={
                        "correlation_id": correlation_id,
                        "infra_id": infra_id,
                        "user_id": str(user_id),
                        "error": str(e),
                    },
                    exc_info=True,
                )

        transaction.on_commit(_publish)

        logger.info(
            "Infrastructure created — event will be published after commit",
            extra={
                "correlation_id": correlation_id,
                "infra_id": infra_id,
                "user_id": str(user_id),
            },
        )
        return serialized_infra

    def delete_infrastructure(self, user_id, infra_id):
        return self.repo.delete(user_id, infra_id)

    def update_infrastructure(self, user_id, infra_id, update_data):
        infra = self.repo.update(user_id, infra_id, update_data)
        if infra:
            return InfrastructureSerializer.serialize_instance(infra)
        return None