from api.models.infrastructure import Infrastructure

class InfrastructureRepository:
    def get_infrastructure(self, infra_id):
        try:
            return Infrastructure.objects.get(id=infra_id)
        except Infrastructure.DoesNotExist:
            return None

    def upsert_infrastructure(self, infra_data):
        infra_id = infra_data.get('id')
        if not infra_id:
            raise ValueError("Infrastructure ID is required for upsert")
        
        infra, created = Infrastructure.objects.update_or_create(
            id=infra_id,
            defaults=infra_data
        )
        return infra, created
    def is_user_authorized(self, infra_id: str, user_id: str) -> bool:
        """Check if user owns or is invited to the infrastructure."""
        return Infrastructure.objects.filter(
            models.Q(id=infra_id) & 
            (models.Q(user_id=user_id) | models.Q(invited_users__id=user_id))
        ).exists()
