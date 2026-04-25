from api.repositories.application import ApplicationRepository
from api.repositories.infrastructure import InfrastructureRepository
from api.repositories.user import UserRepository
from api.services.application_deployment_service import ApplicationDeploymentService
from api.services.application_cleanup_service import ApplicationCleanupService
from api.services.infrastructure_permissions import InfrastructurePermissions
from shared.resilience.http_client import ResilientHttpClient
from django.db import transaction
from api.services.deployment_queue import DeploymentQueue
from api.messaging.producer.producer import ApplicationEventProducer

import os

import logging
logger = logging.getLogger(__name__)

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
        normalized_url = url[:-4] if url.endswith(".git") else url
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
                    candidate_base = candidate[:-4] if candidate.endswith(".git") else candidate
                    if normalized_url == candidate_base or normalized_url_git == candidate_base + ".git":
                        return  # found — short-circuit immediately

            if len(repos) < 100:
                # Last page reached without a match — definitive rejection
                raise ValueError(f"Selected project {project_remote_url} is not in your inviter's ({inviter_name}) GitHub projects")

        # Exhausted MAX_GITHUB_PAGES without finding or definitively rejecting — treat as transient
        raise ValueError("Unable to verify GitHub repository ownership: too many repositories to scan")

    @transaction.atomic
    def create_application(self, user, data: dict):
        """Create a new application after validating user authorization and infra capacity."""
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
        data["user"] = user
        app = self.app_repo.create(data)
        
        app_id_str = str(app.id)
        infra_id_str = str(app.infrastructure_id)
        transaction.on_commit(lambda: DeploymentQueue.enqueue_deployment(app_id_str, infra_id_str))

        ApplicationEventProducer.publish_application_created(
            app.id, app.infrastructure_id, app.name, user.id
        )
        
        return app

    def get_user_applications(self, user_id: str, infra_id:str):
        """Get all applications belonging to a user."""
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

        infrastructure_id = str(app.infrastructure_id)
        service_arn = app.service_arn
        listener_rule_arn = app.listener_rule_arn
        target_group_arn = app.target_group_arn
        task_definition_arn = app.task_definition_arn

        result = self.app_repo.delete(app_id)

        if any([service_arn, listener_rule_arn, target_group_arn, task_definition_arn]):
            try:
                DeploymentQueue.enqueue_cleanup(
                    app_id=app_id,
                    infrastructure_id=infrastructure_id,
                    service_arn=service_arn,
                    listener_rule_arn=listener_rule_arn,
                    target_group_arn=target_group_arn,
                    task_definition_arn=task_definition_arn,
                )
            except Exception as e:
                logger.error(f"Failed to enqueue cleanup for {app_id}: {e} — AWS resources may need manual cleanup")

        ApplicationEventProducer.publish_application_deleted(app_id)

        return result
    
    def deploy_application(self, app_id: str):
        """Deploy an application to AWS infrastructure."""
        app = self.app_repo.get_by_id(app_id)
        if not app:
            raise ValueError("Application not found")
        
        return self.deployment_service.deploy_application(app)
    
    def update_application(self, user_id: str, app_id: str, update_data: dict):
        """Update application configuration."""
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
            app.name = new_name
            update_fields.append('name')

        if 'description' in update_data:
            app.description = update_data['description']
            update_fields.append('description')
        
        if 'envs' in update_data:
            app.envs = update_data['envs']
            update_fields.append('envs')
        
        if 'port' in update_data:
            port = int(update_data['port'])
            if not (1024 <= port <= 65535):
                raise ValueError("Port must be between 1024 and 65535")
            app.port = port
            update_fields.append('port')
        
        if 'alloted_cpu' in update_data or 'alloted_memory' in update_data:
            new_cpu = float(update_data.get('alloted_cpu', app.alloted_cpu))
            new_mem = float(update_data.get('alloted_memory', app.alloted_memory))
            
            valid_combinations = {
                0.25: (0.5, 2.0), 0.5: (1.0, 4.0), 1.0: (2.0, 8.0),
                2.0: (4.0, 16.0), 4.0: (8.0, 30.0)
            }
            if new_cpu not in valid_combinations:
                raise ValueError(f"Invalid CPU. Must be one of: {list(valid_combinations.keys())}")
            min_mem, max_mem = valid_combinations[new_cpu]
            if not (min_mem <= new_mem <= max_mem):
                raise ValueError(f"For {new_cpu} vCPU, memory must be {min_mem}-{max_mem}GB")
            
            # Check infrastructure quota
            infra = self.infra_repo.get_infrastructure(app.infrastructure_id)
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
            ApplicationEventProducer.publish_application_updated(app)

        return app
