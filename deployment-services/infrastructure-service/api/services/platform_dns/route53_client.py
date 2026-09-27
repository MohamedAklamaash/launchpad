"""Route53 client construction and the mock/real fail-closed gate for the DNS writer.

Mirrors the existing `is_mock` / `dev_mode` gate in TerraformWorker.provision()/destroy():
a mock infrastructure outside dev mode, or a real infrastructure inside dev mode, is always
refused rather than silently handled either way. Production with an unconfigured platform
zone fails closed (the writer refuses to start — see run_dns_writer.py) rather than quietly
skipping DNS work and reporting an environment ACTIVE with a URL that will never resolve.
"""
import logging
import os
from dataclasses import dataclass

from . import naming

logger = logging.getLogger(__name__)

DNS_WRITER_IAM_ARN_SUFFIX = "user/launchpad-platform-dns-writer"


class PlatformDnsMisconfigured(RuntimeError):
    """The platform DNS zone is not configured for a path that requires it."""


class MockRealMismatch(RuntimeError):
    """A real infrastructure was asked for in dev/mock mode, or vice versa."""


@dataclass(frozen=True, slots=True)
class PlatformDnsZoneConfig:
    base_domain: str
    zone_id: str
    account_id: str
    access_key_id: str
    secret_access_key: str
    region: str = "us-east-1"


def load_platform_dns_config() -> PlatformDnsZoneConfig | None:
    """None if any required piece is missing — callers decide whether that's fatal
    (the writer at startup, in prod) or fine (mock/dev never needs a real zone)."""
    base_domain = _base_domain()
    zone_id = os.environ.get("PLATFORM_DNS_ZONE_ID", "")
    account_id = os.environ.get("PLATFORM_DNS_ACCOUNT_ID", "")
    access_key_id = os.environ.get("PLATFORM_DNS_ACCESS_KEY_ID", "")
    secret_access_key = os.environ.get("PLATFORM_DNS_SECRET_ACCESS_KEY", "")
    if not all([base_domain, zone_id, account_id, access_key_id, secret_access_key]):
        return None
    return PlatformDnsZoneConfig(
        base_domain=base_domain,
        zone_id=zone_id,
        account_id=account_id,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )


def _base_domain() -> str:
    from django.conf import settings
    return settings.PLATFORM_BASE_DOMAIN


def build_real_route53_client(config: PlatformDnsZoneConfig):
    """Explicit keys only — never boto3's default credential chain, which would silently
    pick up an IMDS role or another env var pair meant for a different credential. The
    writer process holds exactly one AWS identity and must use exactly that one."""
    import boto3

    # Belt-and-braces even though explicit keys are passed below: disables the EC2 instance
    # metadata credential lookup boto3 would otherwise still probe for on a fallback path.
    os.environ["AWS_EC2_METADATA_DISABLED"] = "true"

    return boto3.client(
        "route53",
        region_name=config.region,
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
    )


def assert_caller_identity(config: PlatformDnsZoneConfig) -> None:
    """Startup check: the credential this process holds must be the dedicated DNS writer
    user in the dedicated DNS account — not, for example, the platform's main AssumeRole
    principal misrouted here by a configuration mistake."""
    import boto3

    sts = boto3.client(
        "sts",
        region_name=config.region,
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
    )
    identity = sts.get_caller_identity()
    account = identity.get("Account")
    arn = identity.get("Arn", "")
    if account != config.account_id:
        raise PlatformDnsMisconfigured(
            f"platform DNS credential resolved to account {account!r}, expected "
            f"{config.account_id!r}"
        )
    if not arn.endswith(DNS_WRITER_IAM_ARN_SUFFIX):
        raise PlatformDnsMisconfigured(
            f"platform DNS credential ARN {arn!r} is not the dedicated writer user "
            f"({DNS_WRITER_IAM_ARN_SUFFIX!r})"
        )
    logger.info("platform DNS writer identity verified: %s", arn)


class FakeRoute53Zone:
    """In-memory Route53 double for is_mock/dev reconciles and tests.

    Deliberately mirrors the real API's on-the-wire shapes (trailing dot, '\\052' for a
    leading wildcard label) rather than the writer's own canonical form, so a bug in the
    normalize/denormalize boundary shows up against this fake the same way it would against
    real Route53. Records are keyed by (name, type) with the same last-write-wins UPSERT
    semantics as the real API.

    Process-local only. Two dev-mode processes (e.g. a shell reconcile and run_dns_writer)
    do not see each other's writes — acceptable for part 1 since dev/mock never needs to
    survive a process boundary for correctness, only for the tests exercised in-process.
    """

    def __init__(self, zone_id: str = "MOCKZONEID"):
        self.zone_id = zone_id
        self._records: dict[tuple[str, str], dict] = {}

    def list_resource_record_sets(self, HostedZoneId=None):
        record_sets = [
            {
                "Name": name,
                "Type": rtype,
                "TTL": rec["ttl"],
                "ResourceRecords": [{"Value": rec["value"]}],
            }
            for (name, rtype), rec in sorted(self._records.items())
        ]
        return {"ResourceRecordSets": record_sets, "IsTruncated": False}

    def change_resource_record_sets(self, HostedZoneId=None, ChangeBatch=None):
        for change in ChangeBatch["Changes"]:
            rrs = change["ResourceRecordSet"]
            key = (rrs["Name"], rrs["Type"])
            if change["Action"] in ("UPSERT", "CREATE"):
                self._records[key] = {
                    "ttl": rrs["TTL"],
                    "value": rrs["ResourceRecords"][0]["Value"],
                }
            elif change["Action"] == "DELETE":
                self._records.pop(key, None)
        return {"ChangeInfo": {"Id": "/change/MOCK", "Status": "INSYNC"}}


_fake_zone_singleton: FakeRoute53Zone | None = None


def _shared_fake_zone() -> FakeRoute53Zone:
    global _fake_zone_singleton
    if _fake_zone_singleton is None:
        _fake_zone_singleton = FakeRoute53Zone()
    return _fake_zone_singleton


def reset_fake_zone_for_tests() -> None:
    global _fake_zone_singleton
    _fake_zone_singleton = None


def get_route53_client(*, infra_is_mock: bool, dev_mode: bool):
    """Fail-closed mock/real gate. Both mismatches raise — a mock infra outside dev mode,
    or a real infra inside dev mode. Production with the zone unconfigured raises rather
    than silently skipping the write."""
    if infra_is_mock and not dev_mode:
        raise MockRealMismatch("refusing platform DNS write for a mock infrastructure outside dev mode")
    if dev_mode and not infra_is_mock:
        raise MockRealMismatch("refusing real platform DNS write for a mock infrastructure check inside dev mode")

    if dev_mode:
        return _shared_fake_zone(), _shared_fake_zone().zone_id

    config = load_platform_dns_config()
    if config is None:
        raise PlatformDnsMisconfigured(
            "platform DNS zone is not configured (PLATFORM_DNS_ZONE_ID/ACCOUNT_ID/"
            "ACCESS_KEY_ID/SECRET_ACCESS_KEY) — refusing to write in production"
        )
    return build_real_route53_client(config), config.zone_id


__all__ = [
    "DNS_WRITER_IAM_ARN_SUFFIX",
    "FakeRoute53Zone",
    "MockRealMismatch",
    "PlatformDnsMisconfigured",
    "PlatformDnsZoneConfig",
    "assert_caller_identity",
    "build_real_route53_client",
    "get_route53_client",
    "load_platform_dns_config",
    "naming",
    "reset_fake_zone_for_tests",
]
