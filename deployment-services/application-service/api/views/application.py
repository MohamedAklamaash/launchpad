from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from api.services.application_service import ApplicationService
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
                    "memory": app.alloted_memory
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
            "cpu": app.alloted_cpu,
            "memory": app.alloted_memory,
            "storage": app.alloted_storage,
            "url": app.project_remote_url,
            "branch": app.project_branch,
            "envs": app.envs
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
