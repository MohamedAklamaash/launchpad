from dataclasses import dataclass, asdict
from typing import Optional, List, Any, Dict
from uuid import UUID
from datetime import datetime

@dataclass
class InfrastructureCreateInput:
    name: str
    cloud_provider: str
    max_cpu: float
    max_memory: float
    metadata: Optional[Dict[str, Any]] = None

@dataclass
class InfrastructureUpdateInput:
    name: Optional[str] = None
    cloud_provider: Optional[str] = None
    max_cpu: Optional[float] = None
    max_memory: Optional[float] = None
    is_cloud_authenticated: Optional[bool] = None
    code: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

@dataclass
class InfrastructureResponse:
    id: UUID
    name: str
    user_id: UUID
    cloud_provider: str
    max_cpu: float
    max_memory: float
    is_cloud_authenticated: bool
    metadata: Optional[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    invited_users: List[UUID]

    def to_dict(self):
        data = asdict(self)
        data['id'] = str(self.id)
        data['user_id'] = str(self.user_id)
        data['created_at'] = self.created_at.isoformat()
        data['updated_at'] = self.updated_at.isoformat()
        data['invited_users'] = [str(uid) for uid in self.invited_users]
        data['metadata'] = self.metadata
        data['max_cpu'] = self.max_cpu
        data['max_memory'] = self.max_memory
        data['is_cloud_authenticated'] = self.is_cloud_authenticated
        return data