"""B1 (security review, F1b part 2): provision() must never run against an infrastructure
whose DNS teardown has been requested, or whose environment is DESTROYING/DESTROYED — the
backstop for the primary guard in run_worker.py's TLS ISSUED re-check
(_cert_recheck_eligible), for any other caller that might enqueue a provision on such a row.
"""
import uuid
from unittest.mock import patch

import pytest
from api.models.environment import Environment
from api.services.terraform_worker import TerraformWorker
from django.utils import timezone


@pytest.fixture
def make_infra(db):
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(*, env_status="ACTIVE", dns_teardown_requested=False, exited=False):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="u", role="super_admin"
        )
        infra = Infrastructure.objects.create(
            user=user, name=f"i-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1.0, max_memory=2.0, code="123456789012",
            is_cloud_authenticated=True, metadata={"aws_region": "us-east-1"},
        )
        if dns_teardown_requested:
            Infrastructure.objects.filter(id=infra.id).update(dns_teardown_requested_at=timezone.now())
            infra.refresh_from_db()
        if exited:
            Infrastructure.objects.filter(id=infra.id).update(exited_at=timezone.now())
            infra.refresh_from_db()
        Environment.objects.create(infrastructure=infra, status=env_status)
        return infra

    return _make


def test_provision_refuses_when_infra_has_exited(make_infra):
    """F6's exited_at (owner completed the exit flow) must block a re-provision just like
    dns_teardown_requested_at — asserted independently of that field, belt-and-suspenders."""
    infra = make_infra(exited=True, env_status="ACTIVE")

    with patch("api.services.terraform_worker.authenticate_infrastructure") as auth:
        TerraformWorker.provision(str(infra.id))

    auth.assert_not_called()


def test_provision_refuses_when_dns_teardown_requested(make_infra):
    infra = make_infra(dns_teardown_requested=True, env_status="ERROR")

    with patch("api.services.terraform_worker.authenticate_infrastructure") as auth:
        TerraformWorker.provision(str(infra.id))

    auth.assert_not_called()
    env = Environment.objects.get(infrastructure=infra)
    assert env.status == "ERROR"  # untouched — provision() returned before any status write


@pytest.mark.parametrize("env_status", ["DESTROYING", "DESTROYED"])
def test_provision_refuses_when_environment_is_destroying_or_destroyed(make_infra, env_status):
    infra = make_infra(env_status=env_status)

    with patch("api.services.terraform_worker.authenticate_infrastructure") as auth:
        TerraformWorker.provision(str(infra.id))

    auth.assert_not_called()
    env = Environment.objects.get(infrastructure=infra)
    assert env.status == env_status


def test_provision_proceeds_normally_when_active_and_not_torn_down(make_infra):
    """Sanity check the guard doesn't over-trigger — ACTIVE, no teardown marker, is the
    ordinary re-provision case (e.g. a database create) and must still reach AssumeRole."""
    infra = make_infra(env_status="ACTIVE")

    with patch("api.services.terraform_worker.authenticate_infrastructure", side_effect=RuntimeError("stop here")):
        TerraformWorker.provision(str(infra.id))

    # We don't assert success — the mocked AssumeRole raises deliberately so the test stays
    # fast — only that the guard did not short-circuit before reaching it.
    env = Environment.objects.get(infrastructure=infra)
    assert env.status in ("PROVISIONING", "UPDATING", "ERROR")


def test_provision_still_refuses_mock_real_mismatch_before_the_teardown_guard(make_infra, monkeypatch):
    """The mock/real mismatch check must still run first — a mock infra outside dev mode
    is refused regardless of teardown state, matching destroy()'s own ordering."""
    from api.models.infrastructure import Infrastructure

    infra = make_infra(env_status="ACTIVE")
    Infrastructure.objects.filter(id=infra.id).update(is_mock=True)
    monkeypatch.setenv("MODE", "prod")

    with patch("api.services.terraform_worker.authenticate_infrastructure") as auth:
        TerraformWorker.provision(str(infra.id))

    auth.assert_not_called()
