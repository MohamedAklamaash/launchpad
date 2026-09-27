"""The evidence-pack endpoint costs an AssumeRole plus IAM calls in the customer's
account, same class of call as provisioning logs — it must stay under the gateway's
per-IP limiter, not get exempted."""
from constants import is_rate_limit_exempt


def test_get_evidence_pack_is_not_exempt():
    assert is_rate_limit_exempt("GET", "/api/infrastructures/abc-123/evidence-pack") is False
    assert is_rate_limit_exempt("GET", "/api/infrastructures/abc-123/evidence-pack/") is False
