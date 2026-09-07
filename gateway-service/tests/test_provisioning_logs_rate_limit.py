"""GET /logs is the largest response body in the product and the limiter runs before any
JWT is verified, so it must stay throttled — unlike the database status GETs."""
from constants import is_rate_limit_exempt


def test_get_provisioning_logs_is_not_exempt():
    assert is_rate_limit_exempt("GET", "/api/infrastructures/abc-123/logs") is False
    assert is_rate_limit_exempt("GET", "/api/infrastructures/abc-123/logs/") is False
