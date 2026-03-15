from typing import List, Dict, Any
from api.models.infrastructure import Infrastructure
from api.types.infrastructure import InfrastructureResponse

class InfrastructureSerializer:
    @staticmethod
    def serialize_instance(instance: Infrastructure) -> Dict[str, Any]:
        invited_users_details = [
            {
                'id': str(u.id),
                'email': u.email,
                'user_name': u.user_name,
                'role': u.role,
            }
            for u in instance.invited_users.all()
        ]
        try:
            env = instance.environments.latest('created_at')
            status = env.status
        except Exception:
            status = "UNKNOWN"

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
            invited_users=invited_users_details,
            status=status,
        )
        return response.to_dict()

    @staticmethod
    def serialize_list(instances: List[Infrastructure]) -> List[Dict[str, Any]]:
        return [InfrastructureSerializer.serialize_instance(inst) for inst in instances]
