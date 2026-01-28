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
