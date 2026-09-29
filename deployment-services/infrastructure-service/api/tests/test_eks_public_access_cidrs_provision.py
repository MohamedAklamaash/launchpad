"""A provision that fails on EKS_PUBLIC_ACCESS_CIDRS must record an actionable,
non-secret error_message, not the redactor's "(N lines withheld)" placeholder.

core/settings.py's startup check (see test_eks_public_access_cidrs_startup.py) keeps this
misconfiguration from ever reaching a real deployment's provisioning worker; this test
covers the worker's own defense (MODE=dev skips the startup check, so a dev worker can
still reach this path) and proves the fix for the redaction bug directly.
"""
import uuid
from unittest.mock import patch

import pytest
from api.services import terraform_worker as tw_mod
from api.services.terraform_worker import TerraformWorker
from api.validators import EKS_PUBLIC_ACCESS_CIDRS_GUIDANCE
from django.utils import timezone
from shared.enums.orchestrator import ComputeType

FAKE_CREDENTIALS = {
    "aws_access_key_id": "AKIAFAKE",
    "aws_secret_access_key": "fake-secret",
    "aws_session_token": "fake-session-token",
}


@pytest.fixture
def real_eks_infra(db):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(
        id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="u", role="super_admin"
    )
    infra = Infrastructure.objects.create(
        user=user, name=f"i-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=1.0, max_memory=2.0, code="123456789012",
        compute_type=ComputeType.EKS, metadata={"aws_region": "us-east-1"},
    )
    env = Environment.objects.create(infrastructure=infra, status="PENDING")
    return infra, env


@pytest.mark.django_db
def test_provision_with_bad_eks_cidrs_records_actionable_error_message(settings, real_eks_infra):
    infra, env = real_eks_infra
    settings.EKS_PUBLIC_ACCESS_CIDRS = []

    with patch.object(tw_mod, "is_dev_mode", return_value=False), \
            patch.object(tw_mod, "authenticate_infrastructure", return_value=FAKE_CREDENTIALS), \
            patch.object(TerraformWorker, "_ensure_backend_with_iam_retry", return_value=("bucket-x", "table-x")):
        TerraformWorker.provision(str(infra.id))

    env.refresh_from_db()
    assert env.status == "ERROR"
    assert EKS_PUBLIC_ACCESS_CIDRS_GUIDANCE in env.error_message
    assert "withheld" not in env.error_message


@pytest.mark.django_db
def test_reprovision_with_bad_eks_cidrs_also_records_actionable_error_message(settings, real_eks_infra):
    """An already-activated environment takes the other branch in provision()'s except
    handler ("Update failed: <e>", status restored to ACTIVE); the guidance message
    must survive that template's redaction too, not just the first-provision branch."""
    infra, env = real_eks_infra
    env.first_activated_at = timezone.now()
    env.save(update_fields=["first_activated_at"])
    settings.EKS_PUBLIC_ACCESS_CIDRS = []

    with patch.object(tw_mod, "is_dev_mode", return_value=False), \
            patch.object(tw_mod, "authenticate_infrastructure", return_value=FAKE_CREDENTIALS), \
            patch.object(TerraformWorker, "_ensure_backend_with_iam_retry", return_value=("bucket-x", "table-x")):
        TerraformWorker.provision(str(infra.id))

    env.refresh_from_db()
    assert env.status == "ACTIVE"
    assert EKS_PUBLIC_ACCESS_CIDRS_GUIDANCE in env.error_message
    assert "withheld" not in env.error_message
