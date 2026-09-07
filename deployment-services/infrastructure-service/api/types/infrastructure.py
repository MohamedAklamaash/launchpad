from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass
class InfrastructureCreateInput:
    name: str
    cloud_provider: str
    max_cpu: float
    max_memory: float
    metadata: dict[str, Any] | None = None
    compute_type: str = "ecs_fargate"

@dataclass
class InfrastructureUpdateInput:
    name: str | None = None
    cloud_provider: str | None = None
    max_cpu: float | None = None
    max_memory: float | None = None
    is_cloud_authenticated: bool | None = None
    code: str | None = None
    metadata: dict[str, Any] | None = None

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


def _redact_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
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
    metadata: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    invited_users: list[Any]
    status: str = "UNKNOWN"
    is_mock: bool = False
    code: str | None = None
    compute_type: str = "ecs_fargate"

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
