from shared.enums.cloud_provider import CloudProvider
from api.models.user import User

class Infrastructure:
    def __init__(self, name: str, user: User, cloud_provider: CloudProvider, max_cpu: float, max_memory: float, is_cloud_authenticated: bool, metadata: dict):
        self.name = name
        self.user = user
        self.cloud_provider = cloud_provider
        self.max_cpu = max_cpu
        self.max_memory = max_memory
        self.is_cloud_authenticated = is_cloud_authenticated
        self.metadata = metadata

    def __str__(self):
        return f"{self.name} ({self.cloud_provider})"