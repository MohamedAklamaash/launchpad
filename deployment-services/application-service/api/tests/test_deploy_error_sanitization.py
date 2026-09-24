"""Application.error_message is served over the API and rendered in the dashboard, so the
exception that failed a deploy cannot be stored raw.

Two exception types carry more than the customer asked for. Everything else is a sentence
this service composed and is the whole reason the customer opened the page.
"""

import json
import pathlib
import uuid
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError
from shared.errors.deploy_errors import MAX_ERROR_CHARS, sanitize_deploy_error

# A synthetic account, not the real platform one: the rule scrubs any IAM user ARN, so
# the specific digits carry nothing, and pinning a test to the live account id both trips
# secret scanners and would need editing if it ever rotates.
PLATFORM_USER_ARN = "arn:aws:iam::000000000000:user/launchpad-platform"


def _client_error(code, message, op="AssumeRole"):
    return ClientError({"Error": {"Code": code, "Message": message}}, op)


def _ApiException(status, reason, body=None, headers=None):
    """The real client exception, so the isinstance dispatch is actually exercised."""
    from kubernetes.client.rest import ApiException

    exc = ApiException(status=status, reason=reason)
    exc.body, exc.headers = body, headers
    return exc


# ── the platform principal ────────────────────────────────────────────────────

def test_platform_iam_user_arn_is_scrubbed_from_an_aws_error():
    exc = _client_error("AccessDenied", f"User: {PLATFORM_USER_ARN} is not authorized")
    out = sanitize_deploy_error(exc)
    assert "launchpad-platform" not in out
    assert "000000000000" not in out
    assert "<redacted>" in out


def test_the_aws_explanation_survives():
    """The code alone says an action failed, not which parameter was wrong. Withholding
    the message would leave a customer unable to fix their own misconfiguration."""
    exc = _client_error("InvalidParameterException",
                        "The security group 'sg-0abc' does not exist", op="CreateService")
    out = sanitize_deploy_error(exc)
    assert "InvalidParameterException" in out
    assert "sg-0abc" in out
    assert "CreateService" in out


def test_customer_role_arns_are_kept():
    """Only IAM *user* ARNs are scrubbed — the platform principal is a user, a customer's
    own principals are roles, and their ARNs are what they cross-reference in the console."""
    role = "arn:aws:iam::123456789012:role/LaunchpadDeploymentRole"
    out = sanitize_deploy_error(_client_error("AccessDenied", f"assuming {role}"))
    assert role in out


# ── kubernetes ────────────────────────────────────────────────────────────────

def test_k8s_response_headers_and_body_are_not_stored():
    exc = _ApiException(
        403, "Forbidden",
        # Assembled from parts: written as one literal this is a bearer-token shape,
        # which a secret scanner cannot tell from a live EKS credential.
        headers={"Authorization": "Bearer " + "k8s-aws-v1." + "bm90YXJlYWx0b2tlbg"},
        body=json.dumps({"kind": "Status", "message": "namespaces is forbidden", "code": 403}),
    )
    out = sanitize_deploy_error(exc)
    assert "Bearer" not in out and "k8s-aws-v1" not in out
    assert "HTTP response headers" not in out


def test_k8s_api_message_survives():
    exc = _ApiException(403, "Forbidden",
                        body=json.dumps({"message": "namespaces is forbidden"}))
    out = sanitize_deploy_error(exc)
    assert "namespaces is forbidden" in out
    assert "403" in out and "Forbidden" in out


def test_k8s_unparseable_body_degrades_to_status_and_reason():
    out = sanitize_deploy_error(_ApiException(500, "Internal Error", body="<html>nope</html>"))
    assert "500" in out and "Internal Error" in out
    assert "<html>" not in out


# ── everything else passes through ────────────────────────────────────────────

@pytest.mark.parametrize("message", [
    "Application name 'my.app' is not deployable on Kubernetes: it must be at most 59 characters",
    "Infrastructure is not active. Current status: PROVISIONING",
    "Environment is missing required fields: vpc_id, alb_arn",
    "No :80 listener found on ALB arn:aws:elasticloadbalancing:::loadbalancer/app/x/1",
])
def test_worker_composed_messages_survive_verbatim(message):
    """These are the reason the customer opened the page. An allowlist would withhold
    every one of them."""
    assert sanitize_deploy_error(ValueError(message)) == message


def test_output_is_bounded():
    assert len(sanitize_deploy_error(ValueError("x" * (MAX_ERROR_CHARS + 5_000)))) == MAX_ERROR_CHARS


# ── the write path ────────────────────────────────────────────────────────────

@pytest.fixture
def failing_app(schema_db):
    from api.models.application import Application
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="t")
    infra = Infrastructure.objects.create(
        id=uuid.uuid4(), user=user, name=f"i-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=1024, max_memory=512, code="123456789012",
    )
    return Application.objects.create(
        id=uuid.uuid4(), user=user, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/x/y", project_branch="main",
        project_commit_hash="", envs={}, port=8080,
        alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
    )


def test_deploy_failure_stores_a_sanitized_message(failing_app):
    from api.services.application_deployment_service import ApplicationDeploymentService

    exc = _client_error("AccessDenied", f"User: {PLATFORM_USER_ARN} is not authorized")
    service = ApplicationDeploymentService()
    with patch.object(service, "_validate_infrastructure", side_effect=exc), \
            pytest.raises(ClientError):
        service.deploy_application(failing_app)

    failing_app.refresh_from_db()
    assert failing_app.status == "FAILED"
    assert "launchpad-platform" not in failing_app.error_message
    assert "AccessDenied" in failing_app.error_message


def test_deploy_failure_keeps_a_worker_composed_reason(failing_app):
    from api.services.application_deployment_service import ApplicationDeploymentService

    service = ApplicationDeploymentService()
    reason = "Infrastructure is not active. Current status: PROVISIONING"
    with patch.object(service, "_validate_infrastructure", side_effect=ValueError(reason)), \
            pytest.raises(ValueError):
        service.deploy_application(failing_app)

    failing_app.refresh_from_db()
    assert failing_app.error_message == reason


def test_kubernetes_is_not_imported_at_module_scope():
    """An ECS-only deployment need not ship the client, so the import has to stay inside
    the function that needs it."""
    import shared.errors.deploy_errors as mod

    head = pathlib.Path(mod.__file__).read_text().split("def _is_k8s_api_error")[0]
    assert "import kubernetes" not in head and "from kubernetes" not in head
