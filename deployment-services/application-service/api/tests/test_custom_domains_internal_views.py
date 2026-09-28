"""Internal-only attach/detach HTTP views: request validation, error-code mapping, and
that no user-auth context is required (these are pure machine-to-machine calls — see
api/views/custom_domains_internal.py's docstring)."""
import uuid
from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory

from api.views.custom_domains_internal import (
    application_summary_for_custom_domains,
    custom_domain_attach,
    custom_domain_detach,
)

factory = APIRequestFactory()


def test_attach_rejects_invalid_body():
    request = factory.post("/api/v1/internal/custom-domains/attach/", {}, format="json")

    response = custom_domain_attach(request)

    assert response.status_code == 400


@pytest.mark.django_db
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


@pytest.mark.django_db
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


def test_detach_returns_200_when_confirmed_clean():
    infra_id = uuid.uuid4()
    request = factory.post(
        "/api/v1/internal/custom-domains/detach/",
        {"infrastructure_id": str(infra_id), "hostname": "app.example.com"}, format="json",
    )

    with patch("api.views.custom_domains_internal.detach_custom_domain", return_value=True) as mock_detach:
        response = custom_domain_detach(request)

    assert response.status_code == 200
    assert response.data == {"detached": True}
    mock_detach.assert_called_once_with(infra_id, "app.example.com", infra_tearing_down=False)


def test_detach_forwards_infra_tearing_down_flag():
    infra_id = uuid.uuid4()
    request = factory.post(
        "/api/v1/internal/custom-domains/detach/",
        {"infrastructure_id": str(infra_id), "hostname": "app.example.com", "infra_tearing_down": True},
        format="json",
    )

    with patch("api.views.custom_domains_internal.detach_custom_domain", return_value=True) as mock_detach:
        response = custom_domain_detach(request)

    assert response.status_code == 200
    mock_detach.assert_called_once_with(infra_id, "app.example.com", infra_tearing_down=True)


def test_detach_returns_502_when_aws_side_not_confirmed():
    """Security review R2: infrastructure-service must treat anything but 200 as "not
    confirmed, retry" — a genuine AWS-side failure must not look like success."""
    infra_id = uuid.uuid4()
    request = factory.post(
        "/api/v1/internal/custom-domains/detach/",
        {"infrastructure_id": str(infra_id), "hostname": "app.example.com"}, format="json",
    )

    with patch("api.views.custom_domains_internal.detach_custom_domain", return_value=False):
        response = custom_domain_detach(request)

    assert response.status_code == 502
    assert response.data == {"detached": False}


@pytest.mark.django_db
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


@pytest.mark.django_db
def test_application_summary_returns_narrow_fields_only():
    from types import SimpleNamespace

    from rest_framework.test import force_authenticate

    from api.models.application import Application
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="t")
    infra = Infrastructure.objects.create(
        user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws", max_cpu=4, max_memory=8,
    )
    app = Application.objects.create(
        user=user, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/x/y", project_branch="main",
        project_commit_hash="", envs={"SECRET_KEY": "sk-should-never-leave-this-service"},
        github_webhook_secret="whsec-should-never-leave-this-service",
    )

    request = factory.get(f"/api/v1/internal/applications/{app.id}/summary/")
    force_authenticate(request, user=SimpleNamespace(id=user.id))

    response = application_summary_for_custom_domains(request, str(app.id))

    assert response.status_code == 200
    assert response.data == {"id": str(app.id), "infrastructure_id": str(infra.id), "status": app.status}
    assert "envs" not in response.data
    assert "github_webhook_secret" not in response.data


@pytest.mark.django_db
def test_application_summary_returns_404_for_a_stranger():
    from types import SimpleNamespace

    from rest_framework.test import force_authenticate

    from api.models.application import Application
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    owner = User.objects.create(id=uuid.uuid4(), email=f"o-{uuid.uuid4()}@e.io", user_name="owner")
    stranger = User.objects.create(id=uuid.uuid4(), email=f"s-{uuid.uuid4()}@e.io", user_name="stranger")
    infra = Infrastructure.objects.create(
        user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws", max_cpu=4, max_memory=8,
    )
    app = Application.objects.create(
        user=owner, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/x/y", project_branch="main", project_commit_hash="",
    )

    request = factory.get(f"/api/v1/internal/applications/{app.id}/summary/")
    force_authenticate(request, user=SimpleNamespace(id=stranger.id))

    response = application_summary_for_custom_domains(request, str(app.id))

    assert response.status_code == 404


def test_application_summary_returns_404_for_malformed_id():
    from types import SimpleNamespace

    from rest_framework.test import force_authenticate

    request = factory.get("/api/v1/internal/applications/not-a-uuid/summary/")
    force_authenticate(request, user=SimpleNamespace(id=uuid.uuid4()))

    response = application_summary_for_custom_domains(request, "not-a-uuid")

    assert response.status_code == 404
