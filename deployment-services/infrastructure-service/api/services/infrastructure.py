import os
import uuid
import logging
from django.db import transaction
from api.repositories.infrastructure import InfrastructureRepository
from api.serializers.infrastructure import InfrastructureSerializer
from api.services.infrastructure_permissions import InfrastructurePermissions
from shared.resilience.circuit_breaker import CircuitBreaker
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
        """Create infrastructure row and mint a single-use onboarding token.

        Authentication and provisioning are deferred to the onboarding callback because the customer
        has not yet run the bootstrap script that creates LaunchpadDeploymentRole — calling
        AssumeRole here would always fail.
        """
        correlation_id = str(uuid.uuid4())

        # Allow-list the client-supplied fields. The serializers are docs-only (never
        # validated), and the repo splats this dict into the model constructor, so
        # without this filter a caller could set server-controlled fields directly
        # (is_cloud_authenticated, onboarding_token_*, id, user) and subvert the
        # onboarding state machine.
        ALLOWED_CREATE_FIELDS = {"name", "cloud_provider", "max_cpu", "max_memory", "code", "metadata"}
        infra_data = {k: v for k, v in infra_data.items() if k in ALLOWED_CREATE_FIELDS}
        if isinstance(infra_data.get("metadata"), dict):
            # Never let a caller seed credential keys into metadata (it's serialized back
            # out, and the worker treats these keys as live STS creds).
            infra_data["metadata"] = {
                k: v for k, v in infra_data["metadata"].items()
                if k not in {"aws_access_key_id", "aws_secret_access_key", "aws_session_token"}
            }
        if infra_data.get("cloud_provider"):
            infra_data["cloud_provider"] = infra_data["cloud_provider"].lower()
        cloud_provider = infra_data.get("cloud_provider")
        if cloud_provider == CloudProvider.AWS and not infra_data.get("code"):
            raise ValueError("AWS Account ID is required in the 'code' field for AWS infrastructure.")

        with transaction.atomic():
            infra = self.repo.create(user_id, infra_data)

            Environment.objects.create(
                infrastructure=infra,
                status='PENDING'
            )

            # Plaintext is returned to the dashboard exactly once; only the hash is persisted.
            onboarding_token = infra.issue_onboarding_token()

            serialized_infra = InfrastructureSerializer.serialize_instance(infra)
            infra_id = serialized_infra["id"]

        logger.info(
            f"Infrastructure {infra_id} created; awaiting onboarding callback to provision",
            extra={"correlation_id": correlation_id, "infra_id": infra_id, "user_id": str(user_id)}
        )
        # Transient response key — never persisted in plaintext, never re-served.
        serialized_infra["onboarding_token"] = onboarding_token
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

                # PROVISIONING/DESTROYING have a Terraform run in flight — deleting now
                # would orphan or race live AWS resources, so block.
                if env.status == 'PROVISIONING':
                    raise ValueError(f"Cannot delete while status is {env.status}. Wait for provisioning to complete.")

                if env.status == 'DESTROYING':
                    raise ValueError("Infrastructure is already being destroyed.")

                if env.status == 'ACTIVE':
                    env.status = 'DESTROYING'
                    env.save(update_fields=['status'])
                    InfraQueue.enqueue_destroy(str(infra_id))
                    logger.info(f"Infrastructure {infra_id} destroy enqueued")
                    return True  # Don't delete DB records yet — worker handles that

                # PENDING (created, onboarding not finished — no AssumeRole, no Terraform),
                # ERROR, or DESTROYED: no live AWS resources, delete immediately. Without
                # this a customer who created an infra but never ran the bootstrap script
                # would be stuck with an undeletable PENDING record.
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
        # Only revoke this infra's membership. The previous code deleted the global
        # User row when they had no other invited infra — an infra-scoped action must
        # never delete a cross-service account (and it cascaded to their owned rows).
        infra.invited_users.remove(target_user)
        return True
    
    def update_infrastructure_config(self, user_id, infra_id, update_data):
        """Update infrastructure configuration and publish event."""
        infra = self.repo.get_by_id(user_id, infra_id)
        if not infra:
            return None
        
        # Check permissions (only SUPER_ADMIN/owner can update)
        if not InfrastructurePermissions.can_update_infrastructure(infra, user_id):
            raise PermissionError("Only the infrastructure owner can update it")
        
        # Validate updatable fields
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