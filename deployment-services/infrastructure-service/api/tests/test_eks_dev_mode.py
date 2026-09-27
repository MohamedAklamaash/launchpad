"""Dev-mode mock parity for EKS: _mock_provision fills the EKS output subset and
never touches _exec_tf, terraform, or a real k8s client."""
import uuid
from unittest.mock import patch

import pytest
from api.cloud_providers.aws import authenticate as auth_mod
from api.services import eks_bootstrap as eb
from api.services import terraform_worker as tw_mod
from api.services.terraform_worker import TerraformWorker
from shared.enums.orchestrator import ComputeType


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    monkeypatch.setattr(tw_mod.time, "sleep", lambda *_a, **_k: None)


@pytest.fixture
def make_mock_eks_infra(db):
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
    Infrastructure.objects.filter(id=infra.id).update(is_mock=True)
    infra.refresh_from_db()
    env = Environment.objects.create(infrastructure=infra, status="PENDING")
    return infra, env


@pytest.mark.django_db
def test_dev_eks_provision_fills_eks_subset_without_terraform_or_k8s(make_mock_eks_infra):
    infra, env = make_mock_eks_infra
    with patch.object(tw_mod, "is_dev_mode", return_value=True), \
            patch.object(auth_mod, "is_dev_mode", return_value=True), \
            patch.object(TerraformWorker, "_exec_tf", side_effect=AssertionError("_exec_tf must not run in dev")), \
            patch.object(eb, "bootstrap_eks_environment", side_effect=AssertionError("bootstrap must not run in dev")), \
            patch.object(tw_mod, "bootstrap_eks_environment", side_effect=AssertionError("bootstrap must not run in dev")), \
            patch("api.messaging.producer.producer.infra_producer"):
        TerraformWorker.provision(str(infra.id))

    env.refresh_from_db()
    assert env.status == "ACTIVE"
    assert env.vpc_id
    assert env.cluster_arn.startswith("arn:aws:eks:")
    assert env.ecr_repository_url
    assert env.alb_dns and env.alb_dns.startswith("dev-mock-")
    assert env.alb_arn is None
    assert env.alb_security_group_id is None
    assert env.target_group_arn is None
    assert env.ecs_task_execution_role_arn is None


# ── F1b part 3a: mock end-to-end for the EKS ISSUED-cert -> eks_ingress_tls_ready gate ────

@pytest.mark.django_db
def test_dev_eks_provision_sets_ingress_tls_ready_when_cert_is_issued(make_mock_eks_infra):
    """EKS has no terraform output for this (the Ingress class patch is a k8s object, not
    a module output) — _mock_provision sets the flag directly, mirroring how it always
    skips the real network call and synthesizes the resulting state in dev mode."""
    from api.models.infrastructure_certificate import InfrastructureCertificate

    infra, env = make_mock_eks_infra
    InfrastructureCertificate.objects.create(
        infrastructure=infra, tls_status=InfrastructureCertificate.TLS_ISSUED,
        cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/mock",
    )

    with patch.object(tw_mod, "is_dev_mode", return_value=True), \
            patch.object(auth_mod, "is_dev_mode", return_value=True), \
            patch.object(TerraformWorker, "_exec_tf", side_effect=AssertionError("_exec_tf must not run in dev")), \
            patch.object(eb, "bootstrap_eks_environment", side_effect=AssertionError("bootstrap must not run in dev")), \
            patch.object(tw_mod, "bootstrap_eks_environment", side_effect=AssertionError("bootstrap must not run in dev")), \
            patch("api.messaging.producer.producer.infra_producer"):
        TerraformWorker.provision(str(infra.id))

    env.refresh_from_db()
    assert env.eks_ingress_tls_ready is True

    from api.services.host_readiness import https_ready_for
    assert https_ready_for(infra, env) is True


@pytest.mark.django_db
def test_dev_eks_provision_leaves_ingress_tls_ready_false_without_a_cert(make_mock_eks_infra):
    infra, env = make_mock_eks_infra

    with patch.object(tw_mod, "is_dev_mode", return_value=True), \
            patch.object(auth_mod, "is_dev_mode", return_value=True), \
            patch.object(TerraformWorker, "_exec_tf", side_effect=AssertionError("_exec_tf must not run in dev")), \
            patch.object(eb, "bootstrap_eks_environment", side_effect=AssertionError("bootstrap must not run in dev")), \
            patch.object(tw_mod, "bootstrap_eks_environment", side_effect=AssertionError("bootstrap must not run in dev")), \
            patch("api.messaging.producer.producer.infra_producer"):
        TerraformWorker.provision(str(infra.id))

    env.refresh_from_db()
    assert env.eks_ingress_tls_ready is False
