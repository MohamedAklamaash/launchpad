import os
import uuid
import logging
from django.db import transaction
from api.repositories.infrastructure import InfrastructureRepository
from api.serializers.infrastructure import InfrastructureSerializer
from shared.resilience.circuit_breaker import CircuitBreaker
from api.messaging.producer.producer import infra_producer
from api.cloud_providers.aws.authenticate import authenticate_infrastructure
from shared.enums.cloud_provider import CloudProvider
from api.models.environment import Environment
from api.services.infra_queue import InfraQueue

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
        """Create infrastructure and enqueue provisioning job"""
        correlation_id = str(uuid.uuid4())
        
        cloud_provider = infra_data.get("cloud_provider")
        if cloud_provider == CloudProvider.AWS and not infra_data.get("code"):
            raise ValueError("AWS Account ID is required in the 'code' field for AWS infrastructure.")

        with transaction.atomic():
            infra = self.repo.create(user_id, infra_data)
            
            if infra.cloud_provider == CloudProvider.AWS:
                try:
                    authenticate_infrastructure(infra)
                except Exception as e:
                    logger.error(f"Cloud authentication failed during creation: {e}")
                    raise ValueError(f"Cloud authentication failed: {str(e)}")
                infra.refresh_from_db()

            env = Environment.objects.create(
                infrastructure=infra,
                status='PENDING'
            )

            serialized_infra = InfrastructureSerializer.serialize_instance(infra)
            infra_id = serialized_infra["id"]

        # Enqueue async provisioning
        transaction.on_commit(lambda: InfraQueue.enqueue_provision(infra_id))

        logger.info(
            f"Infrastructure {infra_id} created, provisioning enqueued",
            extra={"correlation_id": correlation_id, "infra_id": infra_id, "user_id": str(user_id)}
        )
        return serialized_infra

    def delete_infrastructure(self, user_id, infra_id):
        """Delete infrastructure and enqueue destroy job"""
        from api.models.environment import Environment
        
        infra = self.repo.get_by_id(user_id, infra_id)
        if not infra:
            return False
        
        if infra.cloud_provider == CloudProvider.AWS:
            # Check environment status
            try:
                env = Environment.objects.get(infrastructure_id=infra_id)
                
                # Prevent deletion if still provisioning
                if env.status in ['PENDING', 'PROVISIONING']:
                    raise ValueError(
                        f"Cannot delete infrastructure. Status: {env.status}. "
                        "Please wait for provisioning to complete or fail before deleting."
                    )
                
                # Prevent deletion if already destroying
                if env.status == 'DESTROYING':
                    raise ValueError("Infrastructure is already being destroyed.")
                
                # Only destroy if ACTIVE (successfully provisioned)
                if env.status == 'ACTIVE':
                    env.status = 'DESTROYING'
                    env.save(update_fields=['status'])
                    InfraQueue.enqueue_destroy(str(infra_id))
                    logger.info(f"Infrastructure {infra_id} destroy enqueued")
                
                # For ERROR or DESTROYED status, just delete the records
                elif env.status in ['ERROR', 'DESTROYED']:
                    logger.info(f"Infrastructure {infra_id} in {env.status} state, deleting records only")
                    env.delete()
                
            except Environment.DoesNotExist:
                logger.warning(f"No environment found for infrastructure {infra_id}")
        
        return self.repo.delete(user_id, infra_id)

    def update_infrastructure(self, user_id, infra_id, update_data):
        infra = self.repo.update(user_id, infra_id, update_data)
        if infra:
            return InfrastructureSerializer.serialize_instance(infra)
        return None