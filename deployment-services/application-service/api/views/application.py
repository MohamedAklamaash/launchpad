from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from api.services.application_service import ApplicationService
from api.services.deployment_queue import DeploymentQueue
from api.repositories.application import ApplicationRepository
from api.services.application_cleanup_service import ApplicationCleanupService
import logging

logger = logging.getLogger(__name__)

class ApplicationListCreateView(APIView):
    """Handle listing user applications and creating new ones."""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.service = ApplicationService()

    def get(self, request):
        """List all applications for the authenticated user."""
        try:
            user = request.user
            infra_id = request.data.get("infrastructure_id", "")
            if not infra_id:
                raise Exception("Infrastructure ID is required in the request body")
            apps = self.service.get_user_applications(user.id, infra_id)
            data = [
                {
                    "id": str(app.id),
                    "name": app.name,
                    "cpu": app.alloted_cpu,
                    "memory": app.alloted_memory,
                    "port": app.port
                } for app in apps
            ]
            return Response(data)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    def post(self, request):
        """Create a new application."""
        try:
            user = request.user
            app = self.service.create_application(user, request.data)
            return Response({"id": str(app.id), "name": app.name}, status=status.HTTP_201_CREATED)
        except PermissionError as e:
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception("Failed to create application")
            return Response({"error": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class ApplicationDetailDeleteView(APIView):
    """Handle retrieving details and deleting a specific application."""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.service = ApplicationService()

    def get(self, request, pk=None):
        """Get details of a specific application."""
        user = request.user
        app = self.service.get_application_details(user.id, pk)
        if not app:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        
        return Response({
            "id": str(app.id),
            "name": app.name,
            "description": app.description,
            "status": app.status,
            "cpu": app.alloted_cpu,
            "memory": app.alloted_memory,
            "storage": app.alloted_storage,
            "port": app.port,
            "url": app.project_remote_url,
            "branch": app.project_branch,
            "dockerfile_path": app.dockerfile_path,
            "envs": app.envs,
            "deployment_url": app.deployment_url,
            "build_id": app.build_id,
            "error_message": app.error_message,
            "created_at": app.created_at.isoformat() if app.created_at else None,
            "updated_at": app.updated_at.isoformat() if app.updated_at else None
        })

    def delete(self, request, pk=None):
        """Delete an application."""
        try:
            user = request.user
            self.service.delete_application(user.id, pk)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except PermissionError as e:
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

class ApplicationDeployView(APIView):
    """Handle application deployment."""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.service = ApplicationService()
    
    def post(self, request, pk=None):
        """Deploy an application to AWS infrastructure."""
        try:
            
            app_repo = ApplicationRepository()
            app = app_repo.get_by_id(pk)
            if not app:
                return Response({"error": "Application not found"}, status=status.HTTP_404_NOT_FOUND)
            
            DeploymentQueue.enqueue_deployment(pk)
            
            return Response({
                "message": "Deployment queued successfully",
                "application_id": str(pk),
                "status": "QUEUED"
            }, status=status.HTTP_202_ACCEPTED)
            
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception("Failed to queue deployment")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class ApplicationRetryDeployView(APIView):
    """Handle retrying failed deployments."""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.service = ApplicationService()
    
    def post(self, request, pk=None):
        """Retry deployment for a failed application."""
        try:
            
            user = request.user
            app_repo = ApplicationRepository()
            app = app_repo.get_by_id(pk)
            
            if not app:
                return Response({"error": "Application not found"}, status=status.HTTP_404_NOT_FOUND)
            
            if str(app.user_id) != str(user.id):
                return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)
            
            cleanup_service = ApplicationCleanupService()
            try:
                cleanup_service.cleanup_application(app)
                logger.info(f"Cleaned up partial deployment for {app.name}")
            except Exception as e:
                logger.warning(f"Cleanup failed (may not exist): {e}")
            
            # Reset application state
            app.status = 'CREATED'
            app.error_message = None
            app.service_arn = None
            app.task_definition_arn = None
            app.target_group_arn = None
            app.listener_rule_arn = None
            app.save(update_fields=['status', 'error_message', 'service_arn', 'task_definition_arn', 'target_group_arn', 'listener_rule_arn'])
            
            DeploymentQueue.enqueue_deployment(pk)
            
            return Response({
                "message": "Deployment retry queued successfully",
                "application_id": str(pk),
                "status": "QUEUED"
            }, status=status.HTTP_202_ACCEPTED)
            
        except Exception as e:
            logger.exception("Failed to retry deployment")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
