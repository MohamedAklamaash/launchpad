import os
import uuid
import logging
from django.db import transaction
from api.repositories.infrastructure import InfrastructureRepository
from api.serializers.infrastructure import InfrastructureSerializer
from api.services.infrastructure_permissions import InfrastructurePermissions
from shared.resilience.circuit_breaker import CircuitBreaker
from api.messaging.producer.producer import infra_producer
from api.cloud_providers.aws.authenticate import authenticate_infrastructure
from shared.enums.cloud_provider import CloudProvider
from api.models.environment import Environment
from api.services.infra_queue import InfraQueue
from api.models.environment import Environment
import requests
import os

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
        
        # Normalize cloud_provider to lowercase to match enum values ("AWS" -> "aws")
        if isinstance(infra_data, dict):
            infra_data = dict(infra_data)
        else:
            infra_data = dict(infra_data)
        if infra_data.get("cloud_provider"):
            infra_data["cloud_provider"] = infra_data["cloud_provider"].lower()
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
        """Delete infrastructure — enqueues async destroy for ACTIVE infra, immediate delete otherwise."""
        infra = self.repo.get_by_id(user_id, infra_id)
        if not infra:
            return False

        if not InfrastructurePermissions.can_delete_infrastructure(infra, user_id):
            raise PermissionError("Only the infrastructure owner can delete it")

        from api.models import Application
        app_count = Application.objects.filter(infrastructure_id=infra_id).count()
        if app_count > 0:
            raise ValueError(f"Cannot delete infrastructure. {app_count} application(s) still exist. Delete all applications first.")

        if infra.cloud_provider == CloudProvider.AWS:
            try:
                env = Environment.objects.get(infrastructure_id=infra_id)

                if env.status in ['PENDING', 'PROVISIONING']:
                    raise ValueError(f"Cannot delete while status is {env.status}. Wait for provisioning to complete.")

                if env.status == 'DESTROYING':
                    raise ValueError("Infrastructure is already being destroyed.")

                if env.status == 'ACTIVE':
                    # Enqueue async destroy — worker deletes DB records after Terraform succeeds
                    env.status = 'DESTROYING'
                    env.save(update_fields=['status'])
                    InfraQueue.enqueue_destroy(str(infra_id))
                    logger.info(f"Infrastructure {infra_id} destroy enqueued")
                    return True  # Don't delete DB records yet — worker handles that

                # ERROR or DESTROYED — no live AWS resources, delete immediately
                logger.info(f"Infrastructure {infra_id} in {env.status} state, deleting records")
                env.delete()

            except Environment.DoesNotExist:
                logger.warning(f"No environment found for infrastructure {infra_id}, deleting record")

        return self.repo.delete(user_id, infra_id)

    def remove_invited_user(self, owner_id, infra_id, target_user_id):
        """Remove an invited user from an infrastructure. Delete user if they belong to no other infra."""
        from api.models.user import User
        infra = self.repo.get_by_id(owner_id, infra_id)
        if not infra:
            return False
        if str(infra.user_id) != str(owner_id):
            raise PermissionError("Only the infrastructure owner can remove users")
        try:
            target_user = User.objects.get(id=target_user_id)
        except User.DoesNotExist:
            return False
        infra.invited_users.remove(target_user)
        # Delete user if they no longer belong to any infrastructure
        if not target_user.invited_infrastructures.exists():
            target_user.delete()
        return True
        infra = self.repo.update(user_id, infra_id, update_data)
        if infra:
            return InfrastructureSerializer.serialize_instance(infra)
        return None
    
    def update_infrastructure_config(self, user_id, infra_id, update_data):
        """Update infrastructure configuration and publish event."""
        infra = self.repo.get_by_id(user_id, infra_id)
        if not infra:
            return None
        
        # Check permissions (only SUPER_ADMIN/owner can update)
        if not InfrastructurePermissions.can_update_infrastructure(infra, user_id):
            raise PermissionError("Only the infrastructure owner can update it")
        
        # Validate updatable fields
        allowed_fields = ['name', 'max_cpu', 'max_memory']
        update_fields = []
        
        if 'name' in update_data:
            infra.name = update_data['name']
            update_fields.append('name')
        
        if 'max_cpu' in update_data or 'max_memory' in update_data:
            new_cpu = float(update_data.get('max_cpu', infra.max_cpu))
            new_mem = float(update_data.get('max_memory', infra.max_memory))
            
            # Note: Infrastructure service doesn't have Application model
            # Validation against current usage should be done in application service
            # or via an API call to application service
            # For now, we'll allow the update and rely on application service validation
            
            infra.max_cpu = new_cpu
            infra.max_memory = new_mem
            update_fields.extend(['max_cpu', 'max_memory'])
        
        if update_fields:
            infra.save(update_fields=update_fields)
            
            # Publish infrastructure.updated event
            from django.db import transaction
            correlation_id = str(uuid.uuid4())
            
            def publish_event():
                from api.messaging.producer.producer import infra_producer
                infra_producer.publish_infrastructure_updated(
                    user_id=infra.user_id,
                    infra_id=infra.id,
                    name=infra.name,
                    max_cpu=infra.max_cpu,
                    max_memory=infra.max_memory,
                    correlation_id=correlation_id
                )
            
            transaction.on_commit(publish_event)
        
        return InfrastructureSerializer.serialize_instance(infra)