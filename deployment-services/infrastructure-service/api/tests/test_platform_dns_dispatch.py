"""process_reconcile_message — the testable core of run_dns_writer's on_message.

Covers the two fixes the pre-review pass flagged: a hostile/wrong-region alb_dns must be a
discard, never a requeue (a customer-writable EKS Ingress status could redeliver forever
and, with prefetch_count=1, starve every other infra behind it); and the coalescing key
must be cleared before converging, not left to expire, or a reconcile that arrives while an
earlier one for the same infra is still being processed is silently dropped.
"""
import json
import uuid

import pytest
from api.services.platform_dns.dispatch import (
    MalformedReconcileMessage,
    process_reconcile_message,
)
from api.services.platform_dns.naming import InvalidDnsRecordError


@pytest.fixture
def make_infra(db):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(*, alb_dns):
        user = User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@e.io", user_name="u", role="super_admin"
        )
        infra = Infrastructure.objects.create(
            user=user, name=f"i-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1.0, max_memory=2.0, code="123456789012",
            is_mock=True, metadata={"aws_region": "us-east-1"},
        )
        infra.mint_dns_label()
        Environment.objects.create(infrastructure=infra, alb_dns=alb_dns, status="ACTIVE")
        return infra

    return _make


def test_malformed_body_raises_malformed_not_generic():
    with pytest.raises(MalformedReconcileMessage):
        process_reconcile_message(b"not json", dev_mode=True)
    with pytest.raises(MalformedReconcileMessage):
        process_reconcile_message(json.dumps({"wrong_key": "x"}).encode(), dev_mode=True)


def test_non_uuid_infrastructure_id_is_malformed_not_a_django_validation_error(db):
    """Django's UUIDField raises ValidationError (not caught anywhere else in the stack) on
    a non-UUID string — without validating the shape up front, that would escape
    parse_reconcile_message as an unclassified exception and be requeued forever instead of
    discarded as the poison message it is."""
    body = json.dumps({"infrastructure_id": "not-a-uuid"}).encode()
    with pytest.raises(MalformedReconcileMessage):
        process_reconcile_message(body, dev_mode=True)


def test_permanent_botocore_error_is_classified_not_requeued_forever(make_infra, monkeypatch):
    """InvalidChangeBatch/InvalidInput from Route53 itself will never succeed on retry —
    must surface as PermanentReconcileError so the caller discards rather than requeues."""
    from api.services.platform_dns.dispatch import PermanentReconcileError
    from botocore.exceptions import ClientError

    infra = make_infra(alb_dns="internal-abc.us-east-1.elb.amazonaws.com")
    body = json.dumps({"infrastructure_id": str(infra.id)}).encode()

    def _raise_invalid_change_batch(*args, **kwargs):
        raise ClientError(
            {"Error": {"Code": "InvalidChangeBatch", "Message": "conflicting change"}},
            "ChangeResourceRecordSets",
        )

    monkeypatch.setattr(
        "api.services.platform_dns.dispatch.converge_platform_dns", _raise_invalid_change_batch,
    )

    with pytest.raises(PermanentReconcileError):
        process_reconcile_message(body, dev_mode=True)


def test_transient_botocore_error_is_not_classified_as_permanent(make_infra, monkeypatch):
    from api.services.platform_dns.dispatch import PermanentReconcileError
    from botocore.exceptions import ClientError

    infra = make_infra(alb_dns="internal-abc.us-east-1.elb.amazonaws.com")
    body = json.dumps({"infrastructure_id": str(infra.id)}).encode()

    def _raise_throttling(*args, **kwargs):
        raise ClientError(
            {"Error": {"Code": "Throttling", "Message": "slow down"}},
            "ChangeResourceRecordSets",
        )

    monkeypatch.setattr(
        "api.services.platform_dns.dispatch.converge_platform_dns", _raise_throttling,
    )

    with pytest.raises(ClientError) as exc_info:
        process_reconcile_message(body, dev_mode=True)
    assert not isinstance(exc_info.value, PermanentReconcileError)


def test_hostile_alb_dns_raises_validation_error_not_requeued_forever(make_infra):
    """A customer-writable EKS Ingress status pointing at a claimable/wrong-region target
    must surface as InvalidDnsRecordError — the caller discards on this, never requeues."""
    infra = make_infra(alb_dns="attacker-bucket.s3.amazonaws.com")
    body = json.dumps({"infrastructure_id": str(infra.id)}).encode()

    with pytest.raises(InvalidDnsRecordError):
        process_reconcile_message(body, dev_mode=True)


def test_reconcile_dedup_key_cleared_before_converging(make_infra, monkeypatch):
    from api.services.platform_dns import producer as producer_mod
    from api.services.platform_dns.route53_client import reset_fake_zone_for_tests

    reset_fake_zone_for_tests()
    infra = make_infra(alb_dns="internal-abc.us-east-1.elb.amazonaws.com")
    body = json.dumps({"infrastructure_id": str(infra.id)}).encode()

    calls = []
    real_clear = producer_mod.clear_reconcile_dedup

    def _spy(infra_id):
        calls.append(infra_id)
        return real_clear(infra_id)

    monkeypatch.setattr("api.services.platform_dns.dispatch.clear_reconcile_dedup", _spy)

    process_reconcile_message(body, dev_mode=True)

    assert calls == [str(infra.id)]
    reset_fake_zone_for_tests()
