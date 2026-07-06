import uuid

import pytest
from shared.enums.user_role import UserRole


@pytest.mark.django_db
def test_missing_per_infra_role_defaults_to_user_not_global_role(schema_db):
    from api.models.user import User
    from api.models.infrastructure import Infrastructure
    from api.models.infrastructure_user_role import InfrastructureUserRole
    from api.repositories.user import UserRepository

    owner = User.objects.create(id=uuid.uuid4(), email=f"o-{uuid.uuid4()}@e.io", user_name="o")
    infra = Infrastructure.objects.create(
        user=owner, name="i", cloud_provider="aws", max_cpu=1, max_memory=1, code="123456789012"
    )
    invited = User.objects.create(id=uuid.uuid4(), email=f"i-{uuid.uuid4()}@e.io", user_name="i")

    UserRepository._link_invited_infrastructures(
        invited,
        {"infra_id": [str(infra.id)], "invited_by": str(owner.id), "role": "admin", "roles": {}},
    )

    edge = InfrastructureUserRole.objects.get(infrastructure=infra, user=invited)
    assert edge.role == UserRole.USER
