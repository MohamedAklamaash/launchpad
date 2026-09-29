"""The dns_writer runs on a least-privilege Postgres role (dns_writer_grants.sql). Found on
real Postgres: publish_host_readiness bumps Infrastructure.host_readiness_version under
SELECT ... FOR UPDATE, which needs UPDATE on at least one column — the grant was missing."""
from pathlib import Path

GRANTS = (Path(__file__).resolve().parents[1] / "services/platform_dns/sql/dns_writer_grants.sql").read_text()


def test_writer_may_update_only_the_readiness_counter_on_infrastructure():
    assert "GRANT UPDATE (host_readiness_version) ON api_infrastructure TO launchpad_dns_writer;" in GRANTS
    assert "GRANT UPDATE ON api_infrastructure" not in GRANTS


def test_writer_never_gets_write_access_to_other_tables():
    for table in ("environments", "api_infrastructurecertificate"):
        assert f"GRANT SELECT ON {table} TO launchpad_dns_writer;" in GRANTS
        assert f"UPDATE ON {table}" not in GRANTS and f"INSERT ON {table}" not in GRANTS
