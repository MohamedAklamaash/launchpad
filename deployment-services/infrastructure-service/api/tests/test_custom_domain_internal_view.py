"""Internal disable-for-application endpoint: request validation and that it delegates
to CustomDomainService.disable_for_application without requiring a user JWT (pure
machine-to-machine, called by application-service's app-delete cleanup)."""
import uuid
from unittest.mock import patch

from api.views.custom_domain_internal import custom_domain_disable_for_application
from rest_framework.test import APIRequestFactory

factory = APIRequestFactory()


def test_rejects_invalid_body():
    request = factory.post("/api/v1/internal/custom-domains/disable-for-application/", {}, format="json")

    response = custom_domain_disable_for_application(request)

    assert response.status_code == 400


def test_delegates_to_service_and_returns_ok():
    infra_id, app_id = uuid.uuid4(), uuid.uuid4()
    request = factory.post(
        "/api/v1/internal/custom-domains/disable-for-application/",
        {"infrastructure_id": str(infra_id), "application_id": str(app_id)}, format="json",
    )

    with patch("api.views.custom_domain_internal.custom_domain_service.disable_for_application") as mock_disable:
        response = custom_domain_disable_for_application(request)

    assert response.status_code == 200
    assert response.data == {"ok": True}
    mock_disable.assert_called_once_with(infra_id, app_id)
