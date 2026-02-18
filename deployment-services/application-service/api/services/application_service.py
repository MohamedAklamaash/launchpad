from api.repositories.application import ApplicationRepository
from api.repositories.infrastructure import InfrastructureRepository
import logging

logger = logging.getLogger(__name__)

class ApplicationService:
    """Primary service for managing applications with resource and auth checks."""
    def __init__(self):
        self.app_repo = ApplicationRepository()
        self.infra_repo = InfrastructureRepository()

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
        return self.app_repo.delete(app_id)
