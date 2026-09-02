import logging
import os
import uuid

from django.conf import settings
from django.db import transaction
from shared.enums.orchestrator import ComputeType
from shared.resilience.http_client import ResilientHttpClient

from api.common.naming import app_slug, require_k8s_safe_slug
from api.messaging.producer.producer import ApplicationEventProducer
from api.models.application import Application
from api.models.infrastructure import Infrastructure
from api.repositories.application import ApplicationRepository
from api.repositories.infrastructure import InfrastructureRepository
from api.repositories.user import UserRepository
from api.serializers.application import FARGATE_CPU_MEMORY
from api.services.application_cleanup_service import ApplicationCleanupService
from api.services.application_deployment_service import ApplicationDeploymentService
from api.services.deployment_lock import DeploymentLock
from api.services.deployment_queue import DeploymentQueue
from api.services.infrastructure_permissions import InfrastructurePermissions

logger = logging.getLogger(__name__)


class DeploymentInProgressError(Exception):
    """Raised when an operation can't proceed because a deploy holds the app's lock."""


class ApplicationService:
    """Primary service for managing applications with resource and auth checks."""
    def __init__(self):
        self.app_repo = ApplicationRepository()
        self.infra_repo = InfrastructureRepository()
        self.deployment_service = ApplicationDeploymentService()
        self.cleanup_service = ApplicationCleanupService()
        
        self.user_client = ResilientHttpClient(
            name="UserServiceClient",
            base_url=os.environ.get("USER_SERVICE_URL", "http://localhost:5002")
        )
        self.infra_client = ResilientHttpClient(
            name="InfraServiceClient",
            base_url=os.environ.get("INFRA_SERVICE_URL", "http://localhost:8002")
        )
        self.user_repo = UserRepository()
        self.github_client = ResilientHttpClient(
            name="GitHubClient",
            base_url="https://api.github.com"
        )
    
    def _validate_github_repo(self, project_remote_url: str, github_token: str, inviter_name: str):
        """Validate that project_remote_url belongs to the inviter's GitHub account.
        Raises ValueError on definitive mismatch, or on transient/API failure.
        Must be called OUTSIDE any transaction.atomic block.
        """
        # Normalise once up front — strip trailing slash and .git suffix
        url = project_remote_url.lower().rstrip("/")
        normalized_url = url.removesuffix(".git")
        # Also build the .git variant for comparison
        normalized_url_git = normalized_url + ".git"

        MAX_GITHUB_PAGES = int(os.environ.get('GITHUB_VALIDATION_MAX_PAGES', '100'))
        for page in range(1, MAX_GITHUB_PAGES + 1):
            response = self.github_client.get(
                f"/user/repos?per_page=100&page={page}",
                headers={"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
            )
            if response.status_code != 200:
                raise ValueError("Unable to verify GitHub repository ownership at this time")

            repos = response.json()
            for r in repos:
                for raw in (r.get("html_url", ""), r.get("clone_url", "")):
                    candidate = raw.lower().rstrip("/")
                    candidate_base = candidate.removesuffix(".git")
                    if normalized_url == candidate_base or normalized_url_git == candidate_base + ".git":
                        return  # found — short-circuit immediately

            if len(repos) < 100:
                # Last page reached without a match — definitive rejection
                raise ValueError(f"Selected project {project_remote_url} is not in your inviter's ({inviter_name}) GitHub projects")

        # Exhausted MAX_GITHUB_PAGES without finding or definitively rejecting — treat as transient
        raise ValueError("Unable to verify GitHub repository ownership: too many repositories to scan")

    # Server-managed / security-sensitive fields a client must never set at create
    # time. The serializer is docs-only and the repo splats this dict into
    # Application.objects.create(**data), so without stripping these a caller could
    # set their own github_webhook_secret (then forge push webhooks), pre-set ARNs,
    # or jump the status — i.e. mass assignment.
    _PROTECTED_CREATE_FIELDS = frozenset({
        "id", "user", "status", "version", "github_webhook_secret",
        "task_definition_arn", "service_arn", "target_group_arn", "listener_rule_arn",
        "deployment_url", "build_id", "error_message", "is_sleeping", "desired_count",
        "runtime_refs",
        "created_at", "updated_at",
    })

    @staticmethod
    def _reserve_slug(infra, name, exclude_application_id=None):
        """Every Kubernetes object name derives from app_slug(name), which lowercases and
        rewrites punctuation. The DB constraint is on `name`, so "MyApp" and "myapp" are
        two rows that collapse onto one namespace and silently overwrite each other's
        workload — across users, since an ADMIN may deploy onto someone else's infra.
        Reject the collision here, under a row lock so two concurrent creates cannot race."""
        if not name:
            raise ValueError("Application name is required")

        infra_compute = getattr(infra, "compute_type", ComputeType.ECS_FARGATE)
        if infra_compute == ComputeType.EKS:
            # Fail before CodeBuild rather than inside the deployer, which runs after the
            # customer has already paid for an image that could never be deployed.
            slug = require_k8s_safe_slug(name)
        else:
            slug = app_slug(name)

        with transaction.atomic():
            Infrastructure.objects.select_for_update().filter(id=infra.id).first()
            clashes = Application.objects.filter(infrastructure_id=infra.id)
            if exclude_application_id:
                clashes = clashes.exclude(id=exclude_application_id)
            for existing in clashes.only("id", "name"):
                if app_slug(existing.name) == slug:
                    raise ValueError(
                        f"An application named '{existing.name}' already exists on this "
                        f"infrastructure and resolves to the same identifier '{slug}'."
                    )
        return slug

    @staticmethod
    def _validate_compute_shape(infra, cpu, memory):
        """The create path builds the model directly, so serializer validation never runs.
        Kubernetes accepts any positive request; only Fargate has a fixed CPU/memory matrix."""
        if cpu <= 0 or memory <= 0:
            raise ValueError("CPU and memory must be greater than zero")
        if getattr(infra, "compute_type", ComputeType.ECS_FARGATE) == ComputeType.EKS:
            if cpu > settings.EKS_MAX_APP_CPU or memory > settings.EKS_MAX_APP_MEMORY:
                raise ValueError(
                    f"Kubernetes applications are limited to {settings.EKS_MAX_APP_CPU} vCPU "
                    f"and {settings.EKS_MAX_APP_MEMORY}GB per application"
                )
            return
        if cpu not in FARGATE_CPU_MEMORY:
            raise ValueError(f"Invalid CPU value. Must be one of: {list(FARGATE_CPU_MEMORY.keys())}")
        min_mem, max_mem = FARGATE_CPU_MEMORY[cpu]
        if not (min_mem <= memory <= max_mem):
            raise ValueError(f"For {cpu} vCPU, memory must be between {min_mem} and {max_mem} GB")

    @transaction.atomic
    def create_application(self, user, data: dict):
        """Create a new application after validating user authorization and infra capacity."""
        data = {k: v for k, v in data.items() if k not in self._PROTECTED_CREATE_FIELDS}
        infra_id = data.get("infrastructure_id")
        if not infra_id:
            raise ValueError("Infrastructure ID is required")

        infra = self.infra_repo.get_infrastructure(infra_id)
        if not infra:
            raise ValueError("Infrastructure not found")
        
        # Check permissions (SUPER_ADMIN or ADMIN can create)
        if not InfrastructurePermissions.can_create_application(infra, user.id):
            raise PermissionError("You don't have permission to create applications. Required role: SUPER_ADMIN or ADMIN")

        requested_cpu = float(data.get("alloted_cpu", 0))
        requested_mem = float(data.get("alloted_memory", 0))

        if requested_cpu > infra.max_cpu or requested_mem > infra.max_memory:
            raise ValueError("Requested resources exceed infrastructure absolute limits")

        totals = self.app_repo.get_total_resources_for_infra(infra_id)
        current_cpu = totals.get("total_cpu") or 0
        current_mem = totals.get("total_memory") or 0

        if (current_cpu + requested_cpu) > infra.max_cpu:
            raise ValueError(f"CPU quota exceeded. Available: {infra.max_cpu - current_cpu}")
        if (current_mem + requested_mem) > infra.max_memory:
            raise ValueError(f"Memory quota exceeded. Available: {infra.max_memory - current_mem}")

        project_remote_url = data.get("project_remote_url", "")
        if not project_remote_url:
            raise ValueError("Project remote url is required")

        self._reserve_slug(infra, data.get("name"))
        self._validate_compute_shape(infra, requested_cpu, requested_mem)

        data["user"] = user
        app = self.app_repo.create(data)
        
        app_id_str = str(app.id)
        infra_id_str = str(app.infrastructure_id)
        transaction.on_commit(lambda: DeploymentQueue.enqueue_deployment(app_id_str, infra_id_str))

        ApplicationEventProducer.publish_application_created(
            app.id, app.infrastructure_id, app.name, user.id
        )
        
        return app

    def get_user_applications(self, user_id: str, infra_id: str):
        """List applications in an infra the caller is allowed to view.

        Gate on membership first — the repo falls back to returning ALL apps in the infra
        when the caller owns none, so without this a non-member could enumerate another
        tenant's app names/ports by passing their infrastructure_id.
        """
        infra = self.infra_repo.get_infrastructure(infra_id)
        if not infra or not InfrastructurePermissions.can_view_application(infra, user_id):
            return []
        return self.app_repo.get_all_for_user(user_id, infra_id)

    def get_application_details(self, user_id: str, app_id: str):
        """Get details of a specific application if user is authorized."""
        app = self.app_repo.get_by_id(app_id)
        if not app:
            return None
        infra = self.infra_repo.get_infrastructure(app.infrastructure_id)
        if not infra or not InfrastructurePermissions.can_view_application(infra, user_id):
            return None
        return app

    def delete_application(self, user_id: str, app_id: str):
        """Delete application DB record immediately; enqueue AWS cleanup async."""
        app = self.app_repo.get_by_id(app_id)
        if not app:
            raise PermissionError("Application not found")

        infra = self.infra_repo.get_infrastructure(app.infrastructure_id)
        if not infra:
            raise ValueError("Infrastructure not found")

        if not InfrastructurePermissions.can_delete_application(infra, user_id):
            raise PermissionError("You don't have permission to delete applications. Required role: SUPER_ADMIN or ADMIN")

        # Serialize with any in-flight deploy. A running deploy holds this lock and calls
        # application.save() repeatedly; deleting the row underneath it resurrects it (UUID PK →
        # save() falls back to INSERT) and orphans the AWS resources the deploy is still creating.
        lock = DeploymentLock()
        lock_owner = f"delete-{uuid.uuid4().hex[:8]}"
        if not lock.acquire(app_id, lock_owner):
            raise DeploymentInProgressError(
                "Application is being deployed; try deleting again once the deploy finishes."
            )
        try:
            infrastructure_id = str(app.infrastructure_id)
            service_arn = app.service_arn
            listener_rule_arn = app.listener_rule_arn
            target_group_arn = app.target_group_arn
            task_definition_arn = app.task_definition_arn
            runtime_refs = app.runtime_refs

            result = self.app_repo.delete(app_id)

            if any([service_arn, listener_rule_arn, target_group_arn, task_definition_arn, runtime_refs]):
                try:
                    DeploymentQueue.enqueue_cleanup(
                        app_id=app_id,
                        infrastructure_id=infrastructure_id,
                        service_arn=service_arn,
                        listener_rule_arn=listener_rule_arn,
                        target_group_arn=target_group_arn,
                        task_definition_arn=task_definition_arn,
                        runtime=(runtime_refs or {}).get('runtime'),
                        refs=runtime_refs,
                    )
                except Exception as e:
                    logger.error(f"Failed to enqueue cleanup for {app_id}: {e} — AWS resources may need manual cleanup")

            ApplicationEventProducer.publish_application_deleted(app_id)
        finally:
            lock.release(app_id, lock_owner)

        return result
    
    def deploy_application(self, app_id: str):
        """Deploy an application to AWS infrastructure."""
        app = self.app_repo.get_by_id(app_id)
        if not app:
            raise ValueError("Application not found")
        
        return self.deployment_service.deploy_application(app)
    
    @transaction.atomic
    def update_application(self, user_id: str, app_id: str, update_data: dict):
        """Update application configuration.

        Transactional so `_reserve_slug`'s row lock is still held when the new name is
        saved; otherwise two concurrent renames can each pass the collision scan and then
        save names that collapse to the same slug — and so the same Kubernetes resources.
        """
        app = self.app_repo.get_by_id(app_id)
        if not app:
            raise PermissionError("Application not found")
        
        infra = self.infra_repo.get_infrastructure(app.infrastructure_id)
        if not infra:
            raise ValueError("Infrastructure not found")
        
        if not InfrastructurePermissions.can_update_application(infra, user_id):
            raise PermissionError("You don't have permission to update applications. Required role: SUPER_ADMIN or ADMIN")
        
        update_fields = []

        if 'name' in update_data:
            new_name = update_data['name'].strip()
            if not new_name:
                raise ValueError("Name cannot be empty")
            self._reserve_slug(infra, new_name, exclude_application_id=app.id)
            app.name = new_name
            update_fields.append('name')

        if 'description' in update_data:
            app.description = update_data['description']
            update_fields.append('description')
        
        if 'envs' in update_data:
            app.envs = update_data['envs']
            update_fields.append('envs')

        if 'attached_database_ids' in update_data:
            if not InfrastructurePermissions.can_attach_database(infra, user_id):
                raise PermissionError(
                    "You don't have permission to attach databases. Required role: SUPER_ADMIN or ADMIN"
                )
            ids = update_data['attached_database_ids']
            if not isinstance(ids, list):
                raise ValueError("attached_database_ids must be a list")
            from api.models.database import Database
            # Same infra, and not already torn down — attaching a database from
            # another tenant's infra or one mid/post-delete must fail loudly here,
            # not surface as a broken deploy later.
            valid_ids = set(
                Database.objects.filter(
                    environment__infrastructure_id=app.infrastructure_id, id__in=ids,
                ).exclude(status__in=['DELETING', 'DELETED']).values_list('id', flat=True)
            )
            requested_ids = {str(i) for i in ids}
            found_ids = {str(i) for i in valid_ids}
            if requested_ids - found_ids:
                raise ValueError(
                    f"Unknown or unavailable database id(s): {sorted(requested_ids - found_ids)}"
                )
            app.attached_database_ids = list(requested_ids)
            update_fields.append('attached_database_ids')

        if 'port' in update_data:
            port = int(update_data['port'])
            if not (1024 <= port <= 65535):
                raise ValueError("Port must be between 1024 and 65535")
            app.port = port
            update_fields.append('port')
        
        if 'alloted_cpu' in update_data or 'alloted_memory' in update_data:
            new_cpu = float(update_data.get('alloted_cpu', app.alloted_cpu))
            new_mem = float(update_data.get('alloted_memory', app.alloted_memory))
            
            # Same rule as the create path: the Fargate matrix applies to ECS only,
            # Kubernetes accepts any positive request.
            self._validate_compute_shape(infra, new_cpu, new_mem)

            # Check infrastructure quota
            totals = self.app_repo.get_total_resources_for_infra(app.infrastructure_id)
            current_cpu = (totals.get("total_cpu") or 0) - app.alloted_cpu
            current_mem = (totals.get("total_memory") or 0) - app.alloted_memory
            
            if (current_cpu + new_cpu) > infra.max_cpu:
                raise ValueError(f"CPU quota exceeded. Available: {infra.max_cpu - current_cpu}")
            if (current_mem + new_mem) > infra.max_memory:
                raise ValueError(f"Memory quota exceeded. Available: {infra.max_memory - current_mem}")
            
            app.alloted_cpu = new_cpu
            app.alloted_memory = new_mem
            update_fields.extend(['alloted_cpu', 'alloted_memory'])
        
        if 'project_branch' in update_data:
            app.project_branch = update_data['project_branch'].strip() or app.project_branch
            update_fields.append('project_branch')

        if 'dockerfile_path' in update_data:
            app.dockerfile_path = update_data['dockerfile_path'].strip() or app.dockerfile_path
            update_fields.append('dockerfile_path')

        if update_fields:
            app.save(update_fields=update_fields)
            transaction.on_commit(lambda: ApplicationEventProducer.publish_application_updated(app))

        return app
