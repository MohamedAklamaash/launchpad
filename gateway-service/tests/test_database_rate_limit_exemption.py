"""Database status GETs are rate-limit exempt; POST /databases (which runs a
synchronous AssumeRole + IAM simulate) must stay throttled."""
from constants import is_rate_limit_exempt


def test_get_database_list_is_exempt():
    assert is_rate_limit_exempt("GET", "/api/infrastructures/abc-123/databases/") is True


def test_get_database_detail_is_exempt():
    assert is_rate_limit_exempt("GET", "/api/infrastructures/abc-123/databases/db-1") is True


def test_post_database_create_is_not_exempt():
    assert is_rate_limit_exempt("POST", "/api/infrastructures/abc-123/databases/") is False


def test_delete_database_is_not_exempt():
    assert is_rate_limit_exempt("DELETE", "/api/infrastructures/abc-123/databases/db-1") is False


def test_unrelated_get_path_is_not_exempt():
    assert is_rate_limit_exempt("GET", "/api/infrastructures/abc-123") is False


def test_exact_match_paths_still_exempt_regardless_of_method():
    assert is_rate_limit_exempt("GET", "/health") is True
    assert is_rate_limit_exempt("POST", "/health") is True
