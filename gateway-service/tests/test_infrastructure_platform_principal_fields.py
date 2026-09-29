"""The dashboard needs the real Launchpad platform AWS account id / IAM user name
(server-derived from LAUNCHPAD_PLATFORM_PRINCIPAL_ARN in infrastructure-service) to
inject into the onboarding/refresh scripts as LAUNCHPAD_PLATFORM_ACCOUNT_ID /
LAUNCHPAD_PLATFORM_USER. proxy_request forwards the upstream JSON body unfiltered
(it returns a raw Response, not FastAPI's response_model-validated object), so these
fields reach the client regardless. This test locks in the OpenAPI contract staying
in sync with that.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _service_urls(monkeypatch):
    for name in (
        "AUTH_SERVICE_URL", "USER_SERVICE_URL", "NOTIFICATION_SERVICE_URL",
        "INFRASTRUCTURE_SERVICE_URL", "APPLICATION_SERVICE_URL", "PAYMENT_SERVICE_URL",
    ):
        monkeypatch.setenv(name, os.environ.get(name, "http://svc:8000"))


def test_infra_response_declares_platform_principal_fields():
    from app.api.endpoints.infrastructure import InfraResponse

    assert "platform_account_id" in InfraResponse.model_fields
    assert "platform_user" in InfraResponse.model_fields


def test_infra_create_response_inherits_platform_principal_fields():
    from app.api.endpoints.infrastructure import InfraCreateResponse

    assert "platform_account_id" in InfraCreateResponse.model_fields
    assert "platform_user" in InfraCreateResponse.model_fields
