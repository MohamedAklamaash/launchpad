"""EKS provision composition: apply -> outputs -> bootstrap -> EKS field map -> ACTIVE,
and the trap case: bootstrap timeout -> inline rollback destroy -> ERROR with the
distinct marker."""
import json
import uuid
from unittest.mock import patch

import pytest
from api.services import terraform_worker as tw_mod
from api.services.eks_bootstrap import (
    ALB_TIMEOUT_MARKER,
    BootstrapResult,
    EksBootstrapTimeout,
)
from api.services.terraform_worker import TerraformWorker
from shared.enums.orchestrator import ComputeType

CREDS = {"aws_access_key_id": "AKIA", "aws_secret_access_key": "s", "aws_session_token": "t"}

TF_OUTPUTS = {
    "vpc_id": {"value": "vpc-0123456789abcdef0"},
    "cluster_arn": {"value": "arn:aws:eks:us-east-1:123456789012:cluster/infra-x"},
    "cluster_name": {"value": "infra-11111111-abcd1234"},
    "cluster_endpoint": {"value": "https://example.eks.amazonaws.com"},
    "ecr_repository_url": {"value": "123456789012.dkr.ecr.us-east-1.amazonaws.com/infra-x"},
}


@pytest.fixture
def make_real_eks_infra(db):
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


def _fake_exec_tf(calls):
    def _exec(cmd, *args, **kwargs):
        calls.append(cmd[1])
        if cmd[1] == "output":
            return {"success": True, "output": json.dumps(TF_OUTPUTS), "logs": "[OUTPUT] ok"}
        return {"success": True, "logs": f"[COMMAND] {cmd[1]} ok"}
    return _exec


@pytest.mark.django_db
def test_eks_provision_happy_path_bootstraps_and_activates(make_real_eks_infra):
    infra, env = make_real_eks_infra
    calls = []
    bootstrap_result = BootstrapResult(
        alb_dns="k8s-abc.us-east-1.elb.amazonaws.com", logs="[phase:alb-wait] ok"
    )
    with patch.object(tw_mod, "authenticate_infrastructure", return_value=dict(CREDS)), \
            patch.object(TerraformWorker, "_exec_tf", side_effect=_fake_exec_tf(calls)), \
            patch.object(tw_mod, "bootstrap_eks_environment", return_value=bootstrap_result) as bootstrap, \
            patch("api.messaging.producer.producer.infra_producer"):
        TerraformWorker.provision(str(infra.id))

    assert calls == ["apply", "output"]
    bootstrap.assert_called_once()
    assert bootstrap.call_args.kwargs["cluster_name"] == "infra-11111111-abcd1234"
    assert bootstrap.call_args.kwargs["credentials"] == CREDS

    env.refresh_from_db()
    assert env.status == "ACTIVE"
    assert env.alb_dns == "k8s-abc.us-east-1.elb.amazonaws.com"
    assert env.vpc_id == "vpc-0123456789abcdef0"
    assert env.cluster_arn.startswith("arn:aws:eks:")
    assert env.ecr_repository_url
    assert env.alb_arn is None
    assert env.alb_security_group_id is None
    assert env.target_group_arn is None
    assert env.ecs_task_execution_role_arn is None
    assert "[BOOTSTRAP]" in env.logs
    assert "[phase:alb-wait] ok" in env.logs
    assert "[phase:apply]" in env.logs


@pytest.mark.django_db
def test_eks_bootstrap_timeout_triggers_rollback_destroy(make_real_eks_infra):
    infra, env = make_real_eks_infra
    calls = []
    timeout = EksBootstrapTimeout(f"{ALB_TIMEOUT_MARKER}: no hostname", logs="[alb-wait] gave up")
    with patch.object(tw_mod, "authenticate_infrastructure", return_value=dict(CREDS)), \
            patch.object(TerraformWorker, "_exec_tf", side_effect=_fake_exec_tf(calls)), \
            patch.object(tw_mod, "bootstrap_eks_environment", side_effect=timeout), \
            patch.object(tw_mod, "cleanup_eks_orphans", return_value="[phase:k8s-reap] done") as reap, \
            patch("api.services.notification.NotificationService"), \
            patch("api.messaging.producer.producer.infra_producer"):
        TerraformWorker.provision(str(infra.id))

    assert calls == ["apply", "output", "destroy"]
    reap.assert_called_once()
    env.refresh_from_db()
    assert env.status == "ERROR"
    assert ALB_TIMEOUT_MARKER in env.error_message
    assert "[phase:k8s-reap]" in env.logs
