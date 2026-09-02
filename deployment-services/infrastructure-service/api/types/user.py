from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass
class UserInfo:
    id: UUID
    email: str
    user_name: str
    role: str
    is_active: bool
    is_staff: bool
    created_at: datetime
    metadata: dict[str, Any] | None = None