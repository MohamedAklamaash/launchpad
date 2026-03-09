from api.repositories.application import ApplicationRepository
from api.repositories.infrastructure import InfrastructureRepository
from api.repositories.user import UserRepository
from api.services.application_deployment_service import ApplicationDeploymentService
from api.services.application_cleanup_service import ApplicationCleanupService
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

        if not self.infra_repo.is_user_authorized(infra_id, user.id):
            raise PermissionError("User is not authorized for this infrastructure")

        infra = self.infra_repo.get_infrastructure(infra_id)
        if not infra:
            raise ValueError("Infrastructure not found")

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
        return self.app_repo.create(data)

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
        """Delete an application if user owns it."""
        app = self.app_repo.get_by_id(app_id)
        if not app or str(app.user_id) != str(user_id):
            raise PermissionError("Application not found or unauthorized")
        
        # Clean up AWS resources before deleting database record
        try:
            self.cleanup_service.cleanup_application(app)
        except Exception as e:
            logger.error(f"Failed to cleanup AWS resources for app {app_id}: {e}")
            # Continue with database deletion even if AWS cleanup fails
        
        return self.app_repo.delete(app_id)
    
    def deploy_application(self, app_id: str):
        """Deploy an application to AWS infrastructure."""
        app = self.app_repo.get_by_id(app_id)
        if not app:
            raise ValueError("Application not found")
        
        return self.deployment_service.deploy_application(app)
