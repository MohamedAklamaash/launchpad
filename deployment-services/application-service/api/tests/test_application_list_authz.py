from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from api.services.application_service import ApplicationService


def _service():
    svc = ApplicationService()
    svc.infra_repo = MagicMock()
    svc.app_repo = MagicMock()
    svc.infra_repo.get_infrastructure.return_value = SimpleNamespace(id="infra-1")
    return svc


def test_non_member_gets_empty_list_not_other_tenants_apps():
    svc = _service()
    with patch(
        "api.services.application_service.InfrastructurePermissions.can_view_application",
        return_value=False,
    ):
        result = svc.get_user_applications("outsider", "infra-1")
    assert result == []
    svc.app_repo.get_all_for_user.assert_not_called()


def test_member_gets_apps():
    svc = _service()
    svc.app_repo.get_all_for_user.return_value = ["app-1"]
    with patch(
        "api.services.application_service.InfrastructurePermissions.can_view_application",
        return_value=True,
    ):
        result = svc.get_user_applications("member", "infra-1")
    assert result == ["app-1"]
