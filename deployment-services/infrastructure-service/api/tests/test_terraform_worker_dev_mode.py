"""SEAM 2 + H3 guard tests for TerraformWorker dev-mode provisioning.

Dev mode drives Environment status PENDING -> PROVISIONING -> ACTIVE, writes all
8 output fields, synthesizes a publish-valid alb_dns / sg / vpc, and never shells
out to terraform (_exec_tf). H3 guard: mock infra refused in prod, real infra
refused in dev.
"""
import re
import uuid
from unittest.mock import patch

import pytest
from api.cloud_providers.aws import authenticate as auth_mod
from api.services import terraform_worker as tw_mod
from api.services.terraform_worker import TerraformWorker

SG_RE = re.compile(r"^sg-[0-9a-f]{8,17}$")
ALB_DNS_RE = re.compile(r"^dev-mock-.*\.elb\.amazonaws\.com$")
VPC_RE = re.compile(r"^vpc-[0-9a-f]{8,17}$")


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    # Keep the ~4s mock-provision delay out of the test.
    monkeypatch.setattr(tw_mod.time, "sleep", lambda *_a, **_k: None)


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(),
            email=f"u-{uuid.uuid4()}@example.com",
            user_name="tester",
        )

    return _make


@pytest.fixture
def make_infra_env(db, make_user):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure

    def _make(*, is_mock=True, code="123456789012", metadata=None):
        infra = Infrastructure.objects.create(
            user=make_user(),
            name=f"infra-{uuid.uuid4()}",
            cloud_provider="aws",
            max_cpu=1.0,
            max_memory=2.0,
            code=code,
            metadata=metadata if metadata is not None else {"aws_region": "us-east-1"},
        )
        if is_mock:
            Infrastructure.objects.filter(id=infra.id).update(is_mock=True)
            infra.refresh_from_db()
        env = Environment.objects.create(infrastructure=infra, status="PENDING")
        return infra, env

    return _make


class _force_mode:
    """Patch is_dev_mode in both the worker and the authenticate seam it calls."""

    def __init__(self, dev: bool):
        self._dev = dev
        self._patches = [
            patch.object(tw_mod, "is_dev_mode", return_value=dev),
            patch.object(auth_mod, "is_dev_mode", return_value=dev),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        return False


def test_dev_provision_drives_status_and_writes_all_outputs(make_infra_env):
    from api.models.environment import Environment

    infra, env = make_infra_env(is_mock=True)
    assert env.status == "PENDING"

    seen_status = []
    orig_save = Environment.save

    def _spy_save(self, *args, **kwargs):
        seen_status.append(self.status)
        return orig_save(self, *args, **kwargs)

    with _force_mode(True), \
            patch.object(TerraformWorker, "_exec_tf", side_effect=AssertionError("_exec_tf must not run in dev")), \
            patch("api.messaging.producer.producer.infra_producer"), \
            patch.object(Environment, "save", _spy_save):
        TerraformWorker.provision(str(infra.id))

    env.refresh_from_db()
    assert env.status == "ACTIVE"
    # PROVISIONING must have been an intermediate state before ACTIVE.
    assert "PROVISIONING" in seen_status
    assert seen_status.index("PROVISIONING") < (
        len(seen_status) - 1 - seen_status[::-1].index("ACTIVE")
    )

    # All 8 output fields populated.
    assert env.vpc_id and VPC_RE.match(env.vpc_id)
    assert env.cluster_arn
    assert env.alb_arn
    assert env.alb_dns and ALB_DNS_RE.match(env.alb_dns)
    assert env.alb_security_group_id and SG_RE.match(env.alb_security_group_id)
    assert env.target_group_arn
    assert env.ecr_repository_url
    assert env.ecs_task_execution_role_arn


def test_dev_provision_never_calls_exec_tf(make_infra_env):
    infra, _env = make_infra_env(is_mock=True)
    with _force_mode(True), \
            patch.object(TerraformWorker, "_exec_tf") as exec_tf, \
            patch("api.messaging.producer.producer.infra_producer"):
        TerraformWorker.provision(str(infra.id))
    exec_tf.assert_not_called()


# --- H3 promotion guard ----------------------------------------------------

def test_provision_refuses_mock_infra_in_prod(make_infra_env):
    infra, env = make_infra_env(is_mock=True)
    with _force_mode(False), \
            patch.object(TerraformWorker, "_exec_tf") as exec_tf:
        # provision() swallows exceptions into Environment.status=ERROR.
        TerraformWorker.provision(str(infra.id))
    exec_tf.assert_not_called()
    env.refresh_from_db()
    assert env.status == "ERROR"
    assert "mock infrastructure" in (env.error_message or "")


def test_provision_refuses_real_infra_in_dev(make_infra_env):
    infra, env = make_infra_env(is_mock=False)
    with _force_mode(True), \
            patch.object(TerraformWorker, "_exec_tf") as exec_tf:
        TerraformWorker.provision(str(infra.id))
    exec_tf.assert_not_called()
    env.refresh_from_db()
    assert env.status == "ERROR"
    assert "real infrastructure" in (env.error_message or "")


def test_dev_destroy_marks_destroyed_without_exec_tf(make_infra_env):
    infra, env = make_infra_env(is_mock=True)
    with _force_mode(True), \
            patch.object(TerraformWorker, "_exec_tf", side_effect=AssertionError("_exec_tf must not run in dev")) as exec_tf:
        TerraformWorker.destroy(str(infra.id))
    exec_tf.assert_not_called()
    env.refresh_from_db()
    assert env.status == "DESTROYED"
    assert env.logs == "[MOCK] destroyed"


def test_destroy_refuses_mock_infra_in_prod(make_infra_env):
    infra, env = make_infra_env(is_mock=True)
    with _force_mode(False), \
            patch.object(TerraformWorker, "_exec_tf") as exec_tf:
        TerraformWorker.destroy(str(infra.id))
    exec_tf.assert_not_called()
    env.refresh_from_db()
    assert env.status != "DESTROYED"


# --- F1b part 3a: mock end-to-end for the ISSUED-cert -> https_listener_arn gate ---------

def test_dev_provision_sets_https_listener_arn_when_cert_is_issued(make_infra_env):
    """Mirrors the real provision()'s enable_https gate: _mock_provision must not set
    https_listener_arn until a certificate is actually ISSUED, and must set it (without any
    real terraform apply) once one is."""
    from api.models.infrastructure_certificate import InfrastructureCertificate

    infra, env = make_infra_env(is_mock=True)
    InfrastructureCertificate.objects.create(
        infrastructure=infra, tls_status=InfrastructureCertificate.TLS_ISSUED,
        cert_arn="arn:aws:acm:us-east-1:123456789012:certificate/mock",
    )

    with _force_mode(True), \
            patch.object(TerraformWorker, "_exec_tf", side_effect=AssertionError("_exec_tf must not run in dev")), \
            patch("api.messaging.producer.producer.infra_producer"):
        TerraformWorker.provision(str(infra.id))

    env.refresh_from_db()
    assert env.https_listener_arn is not None
    assert env.https_listener_arn.startswith("arn:aws:elasticloadbalancing:")

    # host_readiness.https_ready_for reads this exact field back for ECS — see
    # api/tests/test_host_readiness.py for that computation's own direct coverage
    # (transaction.on_commit callbacks, including publish_host_readiness, do not fire
    # inside this test's wrapping transaction, so publishing itself is asserted there).
    from api.services.host_readiness import https_ready_for
    assert https_ready_for(infra, env) is True


def test_dev_provision_leaves_https_listener_arn_unset_without_a_cert(make_infra_env):
    infra, env = make_infra_env(is_mock=True)
    with _force_mode(True), \
            patch.object(TerraformWorker, "_exec_tf", side_effect=AssertionError("_exec_tf must not run in dev")), \
            patch("api.messaging.producer.producer.infra_producer"):
        TerraformWorker.provision(str(infra.id))

    env.refresh_from_db()
    assert env.https_listener_arn is None


def test_destroy_refuses_real_infra_in_dev(make_infra_env):
    infra, env = make_infra_env(is_mock=False)
    with _force_mode(True), \
            patch.object(TerraformWorker, "_exec_tf") as exec_tf:
        TerraformWorker.destroy(str(infra.id))
    exec_tf.assert_not_called()
    env.refresh_from_db()
    assert env.status != "DESTROYED"
