from types import SimpleNamespace
from unittest.mock import MagicMock

from shared.enums.user_role import UserRole
from api.services.infrastructure_permissions import InfrastructurePermissions


def _infra(owner_id, members):
    """members: {user_id: role} for users in invited_users."""
    def _filter(id):
        role = members.get(str(id))
        result = MagicMock()
        result.first.return_value = SimpleNamespace(id=id, role=role) if str(id) in members else None
        result.exists.return_value = str(id) in members
        return result

    invited = MagicMock()
    invited.filter.side_effect = _filter
    return SimpleNamespace(user_id=owner_id, invited_users=invited)


def test_owner_is_super_admin():
    infra = _infra("owner-1", {})
    assert InfrastructurePermissions.get_user_role(infra, "owner-1") == UserRole.SUPER_ADMIN


def test_invited_admin_resolves_to_admin_and_can_manage_apps():
    infra = _infra("owner-1", {"admin-9": UserRole.ADMIN})
    assert InfrastructurePermissions.get_user_role(infra, "admin-9") == UserRole.ADMIN
    assert InfrastructurePermissions.can_create_application(infra, "admin-9") is True


def test_invited_user_stays_read_only():
    infra = _infra("owner-1", {"guest-9": UserRole.USER})
    assert InfrastructurePermissions.get_user_role(infra, "guest-9") == UserRole.USER
    assert InfrastructurePermissions.can_view_application(infra, "guest-9") is True
    assert InfrastructurePermissions.can_create_application(infra, "guest-9") is False


def test_super_admin_member_is_capped_to_admin_not_owner():
    # A globally super_admin user invited to someone else's infra must not become owner-level.
    infra = _infra("owner-1", {"other-sa": UserRole.SUPER_ADMIN})
    assert InfrastructurePermissions.get_user_role(infra, "other-sa") == UserRole.ADMIN
    assert InfrastructurePermissions.can_delete_infrastructure(infra, "other-sa") is False


def test_non_member_gets_no_role_even_if_invited_to_another_infra_of_same_owner():
    # Regression: the old invited_by fallback granted access to EVERY infra owned by the inviter.
    infra_b = _infra("owner-1", {})
    assert InfrastructurePermissions.get_user_role(infra_b, "guest-9") is None
    assert InfrastructurePermissions.can_view_application(infra_b, "guest-9") is False
