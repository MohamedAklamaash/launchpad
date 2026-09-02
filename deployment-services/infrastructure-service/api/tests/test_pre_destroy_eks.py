"""H1/H4 guards for the EKS teardown reap.

The boto3 fallback must fire only on a positive cluster-gone signal, never on a k8s
API failure; deletion requires the non-forgeable VpcId predicate; and the reap runs
from the destroy dispatch and the inline rollback path.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from api.services import eks_teardown as et
from api.services import terraform_worker as tw_mod
from api.services.terraform_worker import MAX_RETRIES, TerraformWorker
from shared.enums.orchestrator import ComputeType


@pytest.fixture
def make_eks_infra(db):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(*, vpc_id="vpc-0123456789abcdef0", env_status="ACTIVE"):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="u", role="super_admin"
        )
        infra = Infrastructure.objects.create(
            user=user, name=f"i-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1.0, max_memory=2.0, code="123456789012",
            compute_type=ComputeType.EKS, metadata={"aws_region": "us-east-1"},
        )
        env = Environment.objects.create(infrastructure=infra, status=env_status, vpc_id=vpc_id)
        return infra, env

    return _make


CREDS = {"aws_access_key_id": "AKIA", "aws_secret_access_key": "s", "aws_session_token": "t"}


def test_noop_for_non_eks_infra():
    infra = SimpleNamespace(compute_type=ComputeType.ECS_FARGATE)
    assert et.cleanup_eks_orphans(infra, credentials=CREDS) == ""


@pytest.mark.django_db
def test_live_cluster_uses_k8s_path_never_fallback(make_eks_infra):
    infra, _ = make_eks_infra()
    with patch.object(et, "_describe_cluster", return_value={"status": "ACTIVE"}), \
            patch.object(et, "boto3"), \
            patch.object(et, "_delete_load_balancer_sources") as k8s_delete, \
            patch.object(et, "_wait_until_load_balancers_reaped") as reap_wait, \
            patch.object(et, "_reap_orphaned_load_balancers") as lb_fallback, \
            patch.object(et, "_reap_orphaned_security_groups") as sg_fallback:
        logs = et.cleanup_eks_orphans(infra, credentials=CREDS)

    k8s_delete.assert_called_once()
    reap_wait.assert_called_once()
    lb_fallback.assert_not_called()
    sg_fallback.assert_not_called()
    assert "[phase:k8s-reap]" in logs


@pytest.mark.django_db
def test_k8s_failure_is_never_a_deletion_signal(make_eks_infra):
    infra, _ = make_eks_infra()
    with patch.object(et, "_describe_cluster", return_value={"status": "ACTIVE"}), \
            patch.object(et, "boto3"), \
            patch.object(et, "_delete_load_balancer_sources", side_effect=TimeoutError("k8s API timed out")), \
            patch.object(et, "_reap_orphaned_load_balancers") as lb_fallback:
        logs = et.cleanup_eks_orphans(infra, credentials=CREDS)

    lb_fallback.assert_not_called()
    assert "non-fatal" in logs


@pytest.mark.django_db
@pytest.mark.parametrize("cluster", [None, {"status": "DELETED"}, {"status": "FAILED"}])
def test_fallback_fires_only_on_positive_signal(make_eks_infra, cluster):
    infra, _ = make_eks_infra()
    with patch.object(et, "_describe_cluster", return_value=cluster), \
            patch.object(et, "boto3"), \
            patch.object(et, "_delete_load_balancer_sources") as k8s_delete, \
            patch.object(et, "_reap_orphaned_load_balancers") as lb_fallback, \
            patch.object(et, "_reap_orphaned_security_groups") as sg_fallback:
        et.cleanup_eks_orphans(infra, credentials=CREDS)

    k8s_delete.assert_not_called()
    lb_fallback.assert_called_once()
    sg_fallback.assert_called_once()


def _fake_elbv2(load_balancers, tags_by_arn):
    elbv2 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{"LoadBalancers": load_balancers}]
    elbv2.get_paginator.return_value = paginator
    elbv2.describe_tags.side_effect = lambda ResourceArns: {
        "TagDescriptions": [
            {"ResourceArn": arn, "Tags": tags_by_arn.get(arn, [])} for arn in ResourceArns
        ]
    }
    return elbv2


CLUSTER = "infra-11111111-abcd1234"
CLUSTER_TAG = [{"Key": et.CLUSTER_TAG_KEY, "Value": CLUSTER}]


def test_matching_requires_both_tag_and_vpc():
    load_balancers = [
        {"LoadBalancerArn": "arn:lb/ours", "VpcId": "vpc-ours"},
        {"LoadBalancerArn": "arn:lb/forged-tag-other-vpc", "VpcId": "vpc-other"},
        {"LoadBalancerArn": "arn:lb/our-vpc-no-tag", "VpcId": "vpc-ours"},
    ]
    tags = {"arn:lb/ours": CLUSTER_TAG, "arn:lb/forged-tag-other-vpc": CLUSTER_TAG}
    matches = et._matching_load_balancers(_fake_elbv2(load_balancers, tags), CLUSTER, "vpc-ours")
    assert [m["LoadBalancerArn"] for m in matches] == ["arn:lb/ours"]


def test_reap_refuses_without_vpc_id():
    session = MagicMock()
    lines = []
    et._reap_orphaned_load_balancers(session, CLUSTER, None, lines)
    session.client.assert_not_called()
    assert any("refusing" in line.lower() for line in lines)


def test_reap_refuses_oversized_batch():
    load_balancers = [
        {"LoadBalancerArn": f"arn:lb/{i}", "VpcId": "vpc-ours"} for i in range(6)
    ]
    tags = {lb["LoadBalancerArn"]: CLUSTER_TAG for lb in load_balancers}
    elbv2 = _fake_elbv2(load_balancers, tags)
    session = MagicMock()
    session.client.return_value = elbv2
    lines = []
    et._reap_orphaned_load_balancers(session, CLUSTER, "vpc-ours", lines)
    elbv2.delete_load_balancer.assert_not_called()
    assert any("REFUSING" in line for line in lines)


def test_reap_logs_match_set_then_deletes():
    load_balancers = [{"LoadBalancerArn": "arn:lb/ours", "VpcId": "vpc-ours"}]
    elbv2 = _fake_elbv2(load_balancers, {"arn:lb/ours": CLUSTER_TAG})
    session = MagicMock()
    session.client.return_value = elbv2
    lines = []
    et._reap_orphaned_load_balancers(session, CLUSTER, "vpc-ours", lines)
    elbv2.delete_load_balancer.assert_called_once_with(LoadBalancerArn="arn:lb/ours")
    assert any("arn:lb/ours" in line for line in lines)


def test_security_group_reap_requires_zero_enis():
    ec2 = MagicMock()
    ec2.describe_security_groups.return_value = {
        "SecurityGroups": [{"GroupId": "sg-attached"}, {"GroupId": "sg-free"}]
    }
    ec2.describe_network_interfaces.side_effect = lambda Filters: {
        "NetworkInterfaces": [{}] if Filters[0]["Values"] == ["sg-attached"] else []
    }
    session = MagicMock()
    session.client.return_value = ec2
    lines = []
    et._reap_orphaned_security_groups(session, CLUSTER, "vpc-ours", lines)
    ec2.delete_security_group.assert_called_once_with(GroupId="sg-free")


# --- entry points ----------------------------------------------------------

@pytest.mark.django_db
def test_rollback_destroy_runs_eks_reap(make_eks_infra):
    infra, env = make_eks_infra(env_status="PROVISIONING")
    result = {"error": "AccessDenied creating cluster", "logs": "[COMMAND] failed"}
    with patch.object(tw_mod, "cleanup_eks_orphans", return_value="[phase:k8s-reap] done") as reap, \
            patch.object(TerraformWorker, "_exec_tf", return_value={"success": True, "logs": ""}), \
            patch("api.services.notification.NotificationService"):
        TerraformWorker._handle_provision_failure(
            str(infra.id), result, {}, CREDS, "us-east-1", "123456789012",
            MAX_RETRIES, ComputeType.EKS,
        )
    reap.assert_called_once()
    env.refresh_from_db()
    assert env.status == "ERROR"
    assert "[phase:k8s-reap]" in env.logs


@pytest.mark.django_db
def test_destroy_dispatch_runs_eks_reap_before_terraform(make_eks_infra):
    infra, env = make_eks_infra()
    call_order = []
    with patch.object(tw_mod, "cleanup_eks_orphans",
                      side_effect=lambda *a, **k: call_order.append("reap") or "[phase:k8s-reap] done") as reap, \
            patch.object(tw_mod, "authenticate_infrastructure", return_value=dict(CREDS)), \
            patch("boto3.client", MagicMock()), \
            patch.object(TerraformWorker, "_exec_tf",
                         side_effect=lambda *a, **k: call_order.append("terraform") or {"success": True, "logs": "[DESTROY] ok"}):
        TerraformWorker.destroy(str(infra.id))

    reap.assert_called_once()
    assert call_order == ["reap", "terraform"]
    env.refresh_from_db()
    assert env.status == "DESTROYED"
    assert "[phase:k8s-reap]" in env.logs
