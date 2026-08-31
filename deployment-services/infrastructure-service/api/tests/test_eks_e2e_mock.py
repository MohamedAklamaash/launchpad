"""Mock-mode E2E for an EKS infrastructure: create -> onboarding callback -> provision
-> ACTIVE with alb_dns -> destroy. Covers the stretch the per-stage EKS tests skip,
namely that compute_type survives the create/onboard boundary and that a mock EKS
infra tears down without ever reaching terraform or a real cluster."""
import uuid
from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework.test import APIRequestFactory

from api.cloud_providers.aws import authenticate as auth_mod
from api.services import terraform_worker as tw_mod
from api.services.infrastructure import InfrastructureService
from api.services.terraform_worker import TerraformWorker
from api.views.infrastructure import infrastructure_onboarding_callback
from shared.enums.orchestrator import ComputeType


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    monkeypatch.setattr(tw_mod.time, "sleep", lambda *_a, **_k: None)


@pytest.fixture
def user(db):
    from api.models.user import User
    return User.objects.create(
        id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="u", role="super_admin"
    )


@pytest.mark.django_db
@override_settings(EKS_ENABLED=True)
def test_eks_infra_survives_create_onboard_provision_destroy(user):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure

    with patch("api.services.infrastructure.is_dev_mode", return_value=True):
        created = InfrastructureService().create_infrastructure(
            user.id,
            {
                "name": f"eks-{uuid.uuid4()}",
                "cloud_provider": "aws",
                "compute_type": "eks",
                "code": "123456789012",
                "max_cpu": 1.0,
                "max_memory": 2.0,
                "metadata": {"aws_region": "us-east-1"},
            },
        )

    assert created["compute_type"] == ComputeType.EKS
    infra_id = created["id"]
    token = created["onboarding_token"]
    assert Environment.objects.get(infrastructure_id=infra_id).status == "PENDING"

    request = APIRequestFactory().post(
        "/api/v1/infrastructures/onboarding/callback",
        {"infra_id": infra_id, "account_id": "123456789012", "onboarding_token": token},
        format="json",
    )
    with patch.object(auth_mod, "is_dev_mode", return_value=True), \
            patch("api.services.infra_queue.InfraQueue.enqueue_provision", return_value=True) as enqueued, \
            patch("api.messaging.producer.producer.infra_producer"):
        response = infrastructure_onboarding_callback(request)

    assert response.status_code == 202, response.data
    assert enqueued.called
    infra = Infrastructure.objects.get(id=infra_id)
    assert infra.is_cloud_authenticated is True
    # The onboarding token is single-use (burned via used_at) and creds are never persisted.
    assert infra.onboarding_token_used_at is not None
    assert not {"aws_access_key_id", "aws_secret_access_key", "aws_session_token"} & (infra.metadata or {}).keys()

    with patch.object(tw_mod, "is_dev_mode", return_value=True), \
            patch.object(auth_mod, "is_dev_mode", return_value=True), \
            patch.object(TerraformWorker, "_exec_tf", side_effect=AssertionError("terraform must not run in dev")), \
            patch("api.messaging.producer.producer.infra_producer"):
        TerraformWorker.provision(str(infra_id))

    env = Environment.objects.get(infrastructure_id=infra_id)
    assert env.status == "ACTIVE"
    assert env.alb_dns
    assert env.cluster_arn.startswith("arn:aws:eks:")
    assert env.ecs_task_execution_role_arn is None

    with patch.object(tw_mod, "is_dev_mode", return_value=True), \
            patch.object(TerraformWorker, "_exec_tf", side_effect=AssertionError("terraform must not run in dev")), \
            patch("api.messaging.producer.producer.infra_producer"):
        TerraformWorker.destroy(str(infra_id))

    env.refresh_from_db()
    assert env.status == "DESTROYED"


@pytest.mark.django_db
@override_settings(EKS_ENABLED=False)
def test_eks_rejected_at_create_when_flag_off(user):
    with patch("api.services.infrastructure.is_dev_mode", return_value=True), \
            pytest.raises(ValueError, match="EKS"):
        InfrastructureService().create_infrastructure(
            user.id,
            {
                "name": f"eks-{uuid.uuid4()}",
                "cloud_provider": "aws",
                "compute_type": "eks",
                "code": "123456789012",
                "max_cpu": 1.0,
                "max_memory": 2.0,
            },
        )
