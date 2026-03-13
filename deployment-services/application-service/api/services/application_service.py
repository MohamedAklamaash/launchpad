from api.repositories.application import ApplicationRepository
from api.repositories.infrastructure import InfrastructureRepository
from api.repositories.user import UserRepository
from api.services.application_deployment_service import ApplicationDeploymentService
from api.services.application_cleanup_service import ApplicationCleanupService
from api.services.infrastructure_permissions import InfrastructurePermissions
from shared.resilience.http_client import ResilientHttpClient
from django.db import transaction
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
        requested_storage = float(data.get("alloted_storage", 0))

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
            raise ValueError(f"Project remote url is required")
        
        if user.invited_by:
            inviter = self.user_repo.get_user(user.invited_by)
            if inviter:
                github_metadata = inviter.metadata.get("github", {}) if inviter.metadata else {}
                github_token = github_metadata.get("token")
                
                if github_token:
                    try:
                        response = self.github_client.get(
                            "/user/repos?per_page=100", 
                            headers={
                                "Authorization": f"token {github_token}",
                                "Accept": "application/vnd.github.v3+json"
                            }
                        )
                        
                        if response.status_code == 200:
                            repos = response.json()
                            allowed_urls = []
                            for r in repos:
                                allowed_urls.append(r.get("html_url", "").lower())
                                allowed_urls.append(r.get("clone_url", "").lower())
                                if r.get("html_url", "").endswith(".git"):
                                    allowed_urls.append(r.get("html_url", "")[:-4].lower())
                                else:
                                    allowed_urls.append((r.get("html_url", "") + ".git").lower())
                                    
                            normalized_url = project_remote_url.lower().rstrip("/")
                            if normalized_url not in allowed_urls:
                                raise ValueError(f"Selected project {project_remote_url} is not in your inviter's ({inviter.user_name}) GitHub projects")
                        else:
                            logger.error(f"Failed to fetch GitHub repos for inviter: {response.status_code} {response.text}")
                            raise ValueError("Unable to verify GitHub repository ownership at this time")
                    except Exception as e:
                        if isinstance(e, ValueError): raise
                        logger.error(f"Error validating GitHub project: {str(e)}")
                        raise ValueError(f"GitHub validation failed: {str(e)}")
                else:
                    logger.warning(f"Inviter {inviter.email} has no GitHub token in metadata")
                    raise ValueError("Your inviter has not linked their GitHub account. Cannot verify project.")
            else:
                raise ValueError(f"User {user.email} has invited_by {user.invited_by} but inviter not found in DB")
        data["user"] = user
        app = self.app_repo.create(data)
        
        # Publish event
        from api.messaging.producer.producer import ApplicationEventProducer
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
        if app and str(app.user_id) == str(user_id):
            return app
        return None

    def delete_application(self, user_id: str, app_id: str):
        """Delete an application if user has permission."""
        app = self.app_repo.get_by_id(app_id)
        if not app:
            raise PermissionError("Application not found")
        
        infra = self.infra_repo.get_infrastructure(app.infrastructure_id)
        if not infra:
            raise ValueError("Infrastructure not found")
        
        # Check permissions (SUPER_ADMIN or ADMIN can delete)
        if not InfrastructurePermissions.can_delete_application(infra, user_id):
            raise PermissionError("You don't have permission to delete applications. Required role: SUPER_ADMIN or ADMIN")
        
        # Clean up AWS resources before deleting database record
        try:
            self.cleanup_service.cleanup_application(app)
        except Exception as e:
            logger.error(f"Failed to cleanup AWS resources for app {app_id}: {e}")
            # Continue with database deletion even if AWS cleanup fails
        
        result = self.app_repo.delete(app_id)
        
        # Publish event
        from api.messaging.producer.producer import ApplicationEventProducer
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
        
        # Check permissions (SUPER_ADMIN or ADMIN can update)
        if not InfrastructurePermissions.can_update_application(infra, user_id):
            raise PermissionError("You don't have permission to update applications. Required role: SUPER_ADMIN or ADMIN")
        
        # Validate updatable fields
        allowed_fields = ['description', 'envs', 'alloted_cpu', 'alloted_memory', 'port']
        update_fields = []
        
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
        
        # Resource updates require quota validation
        if 'alloted_cpu' in update_data or 'alloted_memory' in update_data:
            new_cpu = float(update_data.get('alloted_cpu', app.alloted_cpu))
            new_mem = float(update_data.get('alloted_memory', app.alloted_memory))
            
            # Validate Fargate combinations
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
        
        if update_fields:
            app.save(update_fields=update_fields)
        
        return app
