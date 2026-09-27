"""Custom-domain teardown at infra destroy — F1b part 3b, requirement 9: every entry point
that destroys an infrastructure must clear its custom domains' ALB rules/certs before the
ALB itself is gone, and never let that block the destroy or the DNS teardown (H2). Runs for
both the mock and real destroy paths since TerraformWorker.destroy() short-circuits mock
infras well before _pre_destroy_cleanup/terraform."""
import uuid
from unittest.mock import patch

import pytest


@pytest.fixture
def make_infra(db):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(*, is_mock=False, env_status="ACTIVE"):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="u", role="super_admin",
        )
        infra = Infrastructure.objects.create(
            user=user, name=f"i-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1.0, max_memory=2.0, code="123456789012",
            is_cloud_authenticated=True, metadata={"aws_region": "us-east-1"}, is_mock=is_mock,
        )
        Environment.objects.create(infrastructure=infra, status=env_status)
        return infra

    return _make


def test_mock_destroy_tears_down_custom_domains_before_early_return(make_infra, monkeypatch):
    from types import SimpleNamespace

    from api.services.terraform_worker import TerraformWorker

    infra = make_infra(is_mock=True)
    monkeypatch.setattr("api.services.terraform_worker.app_config", SimpleNamespace(mode="dev"))

    with patch("api.services.terraform_worker.request_and_await_dns_teardown"), \
         patch("api.services.custom_domain_service.CustomDomainService.teardown_for_infrastructure") as teardown:
        TerraformWorker.destroy(str(infra.id))

    teardown.assert_called_once()
    assert teardown.call_args.args[0].id == infra.id


def test_real_destroy_tears_down_custom_domains_before_terraform_runs(make_infra):
    from api.services.terraform_worker import TerraformWorker

    infra = make_infra()
    creds = {"aws_access_key_id": "AKIA", "aws_secret_access_key": "s", "aws_session_token": "t", "account_id": infra.code}
    call_order = []

    def _teardown(self, infra_arg):
        call_order.append(("custom_domain_teardown", infra_arg.id))

    def _exec_tf(*a, **k):
        call_order.append(("exec_tf", None))
        return {"success": True, "logs": "ok"}

    with patch("api.services.terraform_worker.request_and_await_dns_teardown"), \
         patch("api.services.terraform_worker.authenticate_infrastructure", return_value=creds), \
         patch("api.services.terraform_worker.TerraformWorker._pre_destroy_cleanup", return_value=""), \
         patch("api.services.terraform_worker.TerraformWorker._exec_tf", side_effect=_exec_tf), \
         patch("api.services.custom_domain_service.CustomDomainService.teardown_for_infrastructure", _teardown):
        TerraformWorker.destroy(str(infra.id))

    assert call_order[0] == ("custom_domain_teardown", infra.id)
    assert call_order[1][0] == "exec_tf"


def test_destroy_survives_custom_domain_teardown_raising(make_infra):
    """teardown_for_infrastructure already swallows a single domain's failure — this test
    is for the (should-be-unreachable) case of the call itself blowing up, e.g. a DB error
    querying CustomDomain: destroy must still complete."""
    from api.models.environment import Environment
    from api.services.terraform_worker import TerraformWorker

    infra = make_infra()
    creds = {"aws_access_key_id": "AKIA", "aws_secret_access_key": "s", "aws_session_token": "t", "account_id": infra.code}

    with patch("api.services.terraform_worker.request_and_await_dns_teardown"), \
         patch("api.services.terraform_worker.authenticate_infrastructure", return_value=creds), \
         patch("api.services.terraform_worker.TerraformWorker._pre_destroy_cleanup", return_value=""), \
         patch("api.services.terraform_worker.TerraformWorker._exec_tf", return_value={"success": True, "logs": "ok"}), \
         patch("api.services.custom_domain_service.CustomDomainService.teardown_for_infrastructure",
               side_effect=RuntimeError("boom")):
        TerraformWorker.destroy(str(infra.id))

    env = Environment.objects.get(infrastructure_id=infra.id)
    assert env.status == "DESTROYED"
