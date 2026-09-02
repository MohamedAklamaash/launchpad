import ipaddress
import logging
import os
import re
import uuid

from api.common.envs.application import app_config
from api.models.environment import Environment
from api.repositories.infrastructure import InfrastructureRepository
from api.serializers.infrastructure import InfrastructureSerializer
from api.services.infra_queue import InfraQueue
from api.services.infrastructure_permissions import InfrastructurePermissions
from django.db import transaction
from shared.enums.cloud_provider import CloudProvider
from shared.mode import is_dev_mode
from shared.resilience.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

_AWS_ACCOUNT_ID = re.compile(r"[0-9]{12}")
_AWS_REGION = re.compile(r"[a-z]{2}-(gov-)?[a-z]+-\d")


def normalize_account_id(code) -> str:
    """AWS account ids are exactly 12 digits. Reject anything else at the boundary — a malformed
    code otherwise fails deep in AssumeRole and poisons derived S3/DynamoDB backend names."""
    account_id = str(code).strip()
    if not _AWS_ACCOUNT_ID.fullmatch(account_id):
        raise ValueError("AWS Account ID must be exactly 12 digits.")
    return account_id


def validate_vpc_cidr(value) -> None:
    """Reject anything that isn't a strict CIDR literal."""
    try:
        ipaddress.ip_network(str(value), strict=True)
    except ValueError:
        raise ValueError("Invalid vpc_cidr")


def validate_aws_region(value) -> None:
    """Reject anything not shaped like an AWS region name."""
    if not isinstance(value, str) or not _AWS_REGION.fullmatch(value):
        raise ValueError("Invalid aws_region")

cloud_cb = CircuitBreaker(
    name="CloudProviderAPI",
    failure_threshold=int(os.environ.get("CB_FAILURE_THRESHOLD", "5")),
    timeout=float(os.environ.get("CB_TIMEOUT_MS", "30000")) / 1000.0,
    success_threshold=int(os.environ.get("CB_SUCCESS_THRESHOLD", "2"))
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
            # vpc_cidr and aws_region are f-string-interpolated into generated HCL by the
            # terraform worker, so a crafted value could close the block and inject arbitrary
            # config into a subprocess. Shape-validate them at the boundary.
            vpc_cidr = infra_data["metadata"].get("vpc_cidr")
            if vpc_cidr is not None:
                validate_vpc_cidr(vpc_cidr)
            aws_region = infra_data["metadata"].get("aws_region")
            if aws_region is not None:
                validate_aws_region(aws_region)
        if infra_data.get("cloud_provider"):
            infra_data["cloud_provider"] = infra_data["cloud_provider"].lower()
        cloud_provider = infra_data.get("cloud_provider")
        # Only AWS is onboardable end-to-end; anything else persists a permanently
        # un-onboardable junk row (the callback rejects non-AWS). Reject at the boundary.
        if cloud_provider != CloudProvider.AWS:
            raise ValueError("Only AWS is currently supported as a cloud provider.")
        if not infra_data.get("code"):
            raise ValueError("AWS Account ID is required in the 'code' field for AWS infrastructure.")
        infra_data["code"] = normalize_account_id(infra_data["code"])

        dev_mode = is_dev_mode(app_config.mode)
        infra_data.pop("is_mock", None)

        with transaction.atomic():
            infra = self.repo.create(user_id, infra_data)

            if dev_mode:
                infra.is_mock = True
                infra.save(update_fields=["is_mock"])
                logger.warning(
                    "MOCK infrastructure created in dev mode",
                    extra={"infra_id": str(infra.id), "user_id": str(user_id), "is_mock": True},
                )

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

    def reissue_onboarding_token(self, user_id, infra_id):
        """Mint a fresh onboarding token for an infra that isn't onboarded yet, so a user can
        recover from an expired or already-consumed token without deleting and recreating."""
        infra = self.repo.get_by_id(user_id, infra_id)
        if not infra:
            return None
        if not InfrastructurePermissions.can_update_infrastructure(infra, user_id):
            raise PermissionError("Only the infrastructure owner can reissue its onboarding token")
        if infra.is_cloud_authenticated:
            raise ValueError("Infrastructure is already onboarded; no onboarding token is needed.")
        onboarding_token = infra.issue_onboarding_token()
        serialized_infra = InfrastructureSerializer.serialize_instance(infra)
        serialized_infra["onboarding_token"] = onboarding_token
        return serialized_infra

    def _enqueue_env_destroy(self, env, infra_id):
        env.status = 'DESTROYING'
        env.save(update_fields=['status'])
        # A stale per-infra lock (crashed worker) would make enqueue_destroy a silent
        # no-op, leaving the env DESTROYING with no job. A destroy intent must win over a
        # dead provision lock, so force it and re-enqueue. In the rare window where a
        # provision just acquired the lock but hasn't written status=PROVISIONING yet
        # (env still reads ACTIVE), this force can drop that live provision's dedup key;
        # the destroy then loses acquire_db_lock to the heartbeated provision and the
        # env ends ACTIVE — honest state, and a re-delete succeeds.
        if not InfraQueue.enqueue_destroy(str(infra_id)):
            InfraQueue.release_lock(str(infra_id))
            InfraQueue.enqueue_destroy(str(infra_id))
        logger.info(f"Infrastructure {infra_id} destroy enqueued")

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

                # PROVISIONING/UPDATING/DESTROYING have a Terraform run in flight —
                # deleting now would orphan or race live AWS resources, so block.
                if env.status in ('PROVISIONING', 'UPDATING'):
                    raise ValueError(f"Cannot delete while status is {env.status}. Wait for the operation to complete.")

                if env.status == 'DESTROYING':
                    raise ValueError("Infrastructure is already being destroyed.")

                # ERROR only means "no live resources" when it was reached before any
                # AssumeRole/apply ever happened (e.g. a never-onboarded PENDING infra
                # re-enqueued by startup recovery). Once onboarded or ever activated, a
                # real apply may have partially run — including a failed rollback-destroy
                # — so treat it like ACTIVE: async destroy is a safe no-op on empty state.
                never_provisioned = (
                    env.status == 'ERROR'
                    and not infra.is_cloud_authenticated
                    and env.first_activated_at is None
                )
                if env.status == 'ACTIVE' or (env.status == 'ERROR' and not never_provisioned):
                    from api.models.database import Database
                    live_dbs = Database.objects.filter(environment=env).exclude(status='DELETED')
                    if live_dbs.exists():
                        raise ValueError(
                            f"Cannot delete infrastructure. {live_dbs.count()} database(s) still "
                            "exist. Delete all databases first."
                        )
                    self._enqueue_env_destroy(env, infra_id)
                    return True  # Don't delete DB records yet — worker handles that

                # PENDING, DESTROYED, or never-provisioned ERROR: no live AWS resources,
                # delete immediately. Without this a customer who created an infra but
                # never ran the bootstrap script would be stuck with an undeletable record.
                logger.info(f"Infrastructure {infra_id} in {env.status} state, deleting records")
                env.delete()

            except Environment.DoesNotExist:
                logger.warning(f"No environment found for infrastructure {infra_id}, deleting record")

        deleted = self.repo.delete(user_id, infra_id)
        if deleted:
            # Propagate to read-models (application-service) so they drop the row; without this
            # a later infra with the same (user, name) collides on materialization.
            try:
                from api.messaging.producer.producer import infra_producer
                infra_producer.publish_infrastructure_deleted(user_id=user_id, infra_id=infra_id)
            except Exception:
                logger.exception(
                    f"Failed to publish infrastructure.deleted for {infra_id}"
                )
        return deleted

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
        try:
            from api.messaging.producer.producer import infra_producer
            infra_producer.publish_infrastructure_user_removed(
                infra_id=infra_id, removed_user_id=target_user_id
            )
        except Exception:
            logger.exception(
                f"Failed to publish infrastructure.user_removed for {infra_id}/{target_user_id}",
            )
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

        if 'code' in update_data:
            # Correcting a typo'd account id is only safe before onboarding; afterwards the assumed
            # role, state backend, and provisioned resources are all bound to the original account.
            if infra.is_cloud_authenticated:
                raise ValueError("Account ID cannot be changed after onboarding.")
            infra.code = normalize_account_id(update_data['code'])
            update_fields.append('code')
        
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