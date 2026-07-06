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

# The assumed-role STS credentials Launchpad holds for the customer's account are
# stashed inside infrastructure.metadata so the provisioning worker can use them.
# They must NEVER be serialized back to API callers — doing so hands any owner /
# invited user (or anyone who sees a response/log) live keys to the customer's AWS
# account. Strip them at the serialization boundary.
_SENSITIVE_METADATA_KEYS = frozenset({
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "expiration",
})


def _redact_metadata(metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not metadata:
        return metadata
    return {k: v for k, v in metadata.items() if k not in _SENSITIVE_METADATA_KEYS}


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
    invited_users: List[Any]
    status: str = "UNKNOWN"
    is_mock: bool = False
    code: Optional[str] = None

    def to_dict(self):
        data = asdict(self)
        data['id'] = str(self.id)
        data['user_id'] = str(self.user_id)
        data['created_at'] = self.created_at.isoformat()
        data['updated_at'] = self.updated_at.isoformat()
        data['invited_users'] = self.invited_users
        data['metadata'] = _redact_metadata(self.metadata)
        data['max_cpu'] = self.max_cpu
        data['max_memory'] = self.max_memory
        data['is_cloud_authenticated'] = self.is_cloud_authenticated
        data['status'] = self.status
        data['is_mock'] = self.is_mock
        return data
