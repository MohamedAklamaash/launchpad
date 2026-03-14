from api.models.application import Application
from django.db import models

class ApplicationRepository:
    """Repository for Application model operations."""
    def create(self, data: dict) -> Application:
        return Application.objects.create(**data)

    def get_by_id(self, application_id: str) -> Application:
        return Application.objects.filter(id=application_id).first()

    def get_all_for_user(self, user_id: str, infra_id: str) -> models.QuerySet:
        owns_any = Application.objects.filter(user_id=user_id, infrastructure_id=infra_id).exists()
        if owns_any:
            return Application.objects.filter(user_id=user_id, infrastructure_id=infra_id)
        return Application.objects.filter(infrastructure_id=infra_id)

    def update(self, application_id: str, data: dict) -> Application:
        Application.objects.filter(id=application_id).update(**data)
        return self.get_by_id(application_id)

    def delete(self, application_id: str) -> bool:
        count, _ = Application.objects.filter(id=application_id).delete()
        return count > 0

    def get_total_resources_for_infra(self, infra_id: str) -> dict:
        return Application.objects.filter(infrastructure_id=infra_id).aggregate(
            total_cpu=models.Sum('alloted_cpu'),
            total_memory=models.Sum('alloted_memory'),
            total_storage=models.Sum('alloted_storage')
        )
