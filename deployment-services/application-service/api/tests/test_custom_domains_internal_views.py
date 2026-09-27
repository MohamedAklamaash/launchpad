"""Internal-only attach/detach HTTP views: request validation, error-code mapping, and
that no user-auth context is required (these are pure machine-to-machine calls — see
api/views/custom_domains_internal.py's docstring)."""
import uuid
from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory

from api.views.custom_domains_internal import custom_domain_attach, custom_domain_detach

factory = APIRequestFactory()


def test_attach_rejects_invalid_body():
    request = factory.post("/api/v1/internal/custom-domains/attach/", {}, format="json")

    response = custom_domain_attach(request)

    assert response.status_code == 400


@pytest.mark.parametrize("exc_name,expected_status", [
    ("CustomDomainNotFound", 404),
    ("CustomDomainNotReady", 409),
    ("CustomDomainConflict", 409),
    ("SniCapReached", 409),
])
def test_attach_maps_service_errors_to_status_codes(exc_name, expected_status):
    from api.services import custom_domain_routing as routing

    exc_class = getattr(routing, exc_name)
    body = {
        "infrastructure_id": str(uuid.uuid4()), "application_id": str(uuid.uuid4()),
        "hostname": "app.example.com", "cert_arn": "arn:a",
    }
    request = factory.post("/api/v1/internal/custom-domains/attach/", body, format="json")

    with patch("api.views.custom_domains_internal.attach_custom_domain", side_effect=exc_class("boom")):
        response = custom_domain_attach(request)

    assert response.status_code == expected_status


def test_attach_returns_rule_arns_on_success():
    body = {
        "infrastructure_id": str(uuid.uuid4()), "application_id": str(uuid.uuid4()),
        "hostname": "app.example.com", "cert_arn": "arn:a",
    }
    request = factory.post("/api/v1/internal/custom-domains/attach/", body, format="json")

    with patch("api.views.custom_domains_internal.attach_custom_domain",
               return_value={"host_forward_rule_arn": "arn:fwd", "host_redirect_rule_arn": "arn:redir"}) as mock_attach:
        response = custom_domain_attach(request)

    assert response.status_code == 200
    assert response.data == {"host_forward_rule_arn": "arn:fwd", "host_redirect_rule_arn": "arn:redir"}
    mock_attach.assert_called_once_with(
        infrastructure_id=uuid.UUID(body["infrastructure_id"]),
        application_id=uuid.UUID(body["application_id"]),
        hostname="app.example.com", cert_arn="arn:a",
    )


def test_detach_rejects_invalid_body():
    request = factory.post("/api/v1/internal/custom-domains/detach/", {}, format="json")

    response = custom_domain_detach(request)

    assert response.status_code == 400


def test_detach_is_idempotent_and_reports_whether_anything_was_removed():
    request = factory.post("/api/v1/internal/custom-domains/detach/", {"hostname": "app.example.com"}, format="json")

    with patch("api.views.custom_domains_internal.detach_custom_domain", return_value=False) as mock_detach:
        response = custom_domain_detach(request)

    assert response.status_code == 200
    assert response.data == {"detached": False}
    mock_detach.assert_called_once_with("app.example.com")


def test_attach_view_does_not_require_request_user():
    """These are pure machine-to-machine calls — no forwarded JWT, no request.user set by
    any auth middleware (none runs in this direct-call test, matching production where
    these paths are JWT-exempt — see shared/middleware/authentication.py)."""
    body = {
        "infrastructure_id": str(uuid.uuid4()), "application_id": str(uuid.uuid4()),
        "hostname": "app.example.com", "cert_arn": "arn:a",
    }
    request = factory.post("/api/v1/internal/custom-domains/attach/", body, format="json")
    assert not hasattr(request, "user")

    with patch("api.views.custom_domains_internal.attach_custom_domain",
               return_value={"host_forward_rule_arn": "a", "host_redirect_rule_arn": "b"}):
        response = custom_domain_attach(request)

    assert response.status_code == 200
