from dataclasses import dataclass
from typing import Optional, List, Any, Dict
from uuid import UUID
from datetime import datetime

@dataclass
class UserInfo:
    id: UUID
    email: str
    user_name: str
    role: str
    is_active: bool
    is_staff: bool
    created_at: datetime
    metadata: Optional[Dict[str, Any]] = None