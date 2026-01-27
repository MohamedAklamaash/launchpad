from typing import List, Dict, Any
from api.models.infrastructure import Infrastructure
from api.types.infrastructure import InfrastructureResponse

class InfrastructureSerializer:
    @staticmethod
    def serialize_instance(instance: Infrastructure) -> Dict[str, Any]:
        response = InfrastructureResponse(
            id=instance.id,
            name=instance.name,
            user_id=instance.user_id,
            cloud_provider=instance.cloud_provider,
            max_cpu=instance.max_cpu,
            max_memory=instance.max_memory,
            is_cloud_authenticated=instance.is_cloud_authenticated,
            metadata=instance.metadata,
            created_at=instance.created_at,
            updated_at=instance.updated_at,
            invited_users=list(instance.invited_users.values_list('id', flat=True))
        )
        return response.to_dict()

    @staticmethod
    def serialize_list(instances: List[Infrastructure]) -> List[Dict[str, Any]]:
        return [InfrastructureSerializer.serialize_instance(inst) for inst in instances]
