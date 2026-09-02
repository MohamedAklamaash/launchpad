import uuid

import pytest
from shared.enums.user_role import UserRole

from api.services.infrastructure_permissions import InfrastructurePermissions


@pytest.fixture
def make(schema_db):
    from api.models.infrastructure import Infrastructure
    from api.models.infrastructure_user_role import InfrastructureUserRole
    from api.models.user import User

    def _make(member_role=None, as_invited=False):
        owner = User.objects.create(id=uuid.uuid4(), email=f"o-{uuid.uuid4()}@e.io", user_name="o")
        infra = Infrastructure.objects.create(
            user=owner, name="i", cloud_provider="aws", max_cpu=1, max_memory=1, code="123456789012"
        )
        member = User.objects.create(id=uuid.uuid4(), email=f"m-{uuid.uuid4()}@e.io", user_name="m")
        if member_role is not None:
            InfrastructureUserRole.objects.create(infrastructure=infra, user=member, role=member_role)
        if as_invited:
            infra.invited_users.add(member)
        return infra, owner, member

    return _make


@pytest.mark.django_db
def test_owner_is_super_admin(make):
    infra, owner, _ = make()
    assert InfrastructurePermissions.get_user_role(infra, owner.id) == UserRole.SUPER_ADMIN


@pytest.mark.django_db
def test_invited_admin_resolves_to_admin_and_can_manage_apps(make):
    infra, _, member = make(member_role=UserRole.ADMIN)
    assert InfrastructurePermissions.get_user_role(infra, member.id) == UserRole.ADMIN
    assert InfrastructurePermissions.can_create_application(infra, member.id) is True


@pytest.mark.django_db
def test_invited_user_stays_read_only(make):
    infra, _, member = make(member_role=UserRole.USER)
    assert InfrastructurePermissions.get_user_role(infra, member.id) == UserRole.USER
    assert InfrastructurePermissions.can_view_application(infra, member.id) is True
    assert InfrastructurePermissions.can_create_application(infra, member.id) is False


@pytest.mark.django_db
def test_membership_without_role_row_stays_least_privileged(make):
    infra, _, member = make(as_invited=True)
    assert InfrastructurePermissions.get_user_role(infra, member.id) == UserRole.USER


@pytest.mark.django_db
def test_super_admin_member_is_capped_to_admin_not_owner(make):
    infra, _, member = make(member_role=UserRole.SUPER_ADMIN)
    assert InfrastructurePermissions.get_user_role(infra, member.id) == UserRole.ADMIN
    assert InfrastructurePermissions.can_delete_infrastructure(infra, member.id) is False


@pytest.mark.django_db
def test_non_member_gets_no_role(make):
    infra, _, member = make()
    assert InfrastructurePermissions.get_user_role(infra, member.id) is None
    assert InfrastructurePermissions.can_view_application(infra, member.id) is False


@pytest.mark.django_db
def test_role_on_one_infra_grants_nothing_on_another_infra_of_same_owner(schema_db):
    from api.models.infrastructure import Infrastructure
    from api.models.infrastructure_user_role import InfrastructureUserRole
    from api.models.user import User

    owner = User.objects.create(id=uuid.uuid4(), email=f"o-{uuid.uuid4()}@e.io", user_name="o")
    infra_a = Infrastructure.objects.create(
        user=owner, name="a", cloud_provider="aws", max_cpu=1, max_memory=1, code="111111111111"
    )
    infra_b = Infrastructure.objects.create(
        user=owner, name="b", cloud_provider="aws", max_cpu=1, max_memory=1, code="222222222222"
    )
    member = User.objects.create(id=uuid.uuid4(), email=f"m-{uuid.uuid4()}@e.io", user_name="m")
    InfrastructureUserRole.objects.create(infrastructure=infra_b, user=member, role=UserRole.ADMIN)
    infra_b.invited_users.add(member)

    assert InfrastructurePermissions.get_user_role(infra_a, member.id) is None
    assert InfrastructurePermissions.can_view_application(infra_a, member.id) is False
