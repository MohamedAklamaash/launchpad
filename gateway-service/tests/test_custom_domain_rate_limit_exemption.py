"""F0: custom-domain routes are not exempt from the gateway's per-IP rate limit — the F0
per-user budget bucket is an additional, downstream bound (infrastructure-service), not a
replacement for it."""
from constants import is_rate_limit_exempt


def test_get_custom_domain_list_is_not_exempt():
    assert is_rate_limit_exempt("GET", "/api/infrastructures/abc-123/custom-domains/") is False


def test_post_custom_domain_claim_is_not_exempt():
    assert is_rate_limit_exempt("POST", "/api/infrastructures/abc-123/custom-domains/") is False


def test_post_custom_domain_verify_is_not_exempt():
    assert is_rate_limit_exempt("POST", "/api/infrastructures/abc-123/custom-domains/dom-1/verify") is False


def test_delete_custom_domain_is_not_exempt():
    assert is_rate_limit_exempt("DELETE", "/api/infrastructures/abc-123/custom-domains/dom-1") is False
