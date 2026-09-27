"""Builds and gates the platform host URL for an app:
`{slug}.{dns_label}.{PLATFORM_BASE_DOMAIN}` (F1b part 3a — see plan/F1b-tls-activation.md).

Every value interpolated into a hostname here is validated against a strict allow-list
before use — the result of build_app_hostname ends up in nginx config, an ALB host-header
condition, and (on EKS) a Kubernetes Ingress manifest, all config-injection sinks. Path URLs
never go through this module at all; host URLs are strictly additive.
"""
import re

from django.conf import settings

# Exact shape infra-service mints (secrets.token_hex(8)) — mirrors
# infrastructure-service/api/services/platform_dns/naming.py:DNS_LABEL_RE. A dns_label
# reaching this module came off a RabbitMQ event (see
# api/messaging/consumers/infrastructure.py); this is the config-injection boundary check
# applied again immediately before use, not just at consume time.
DNS_LABEL_RE = re.compile(r"^[0-9a-f]{16}$")


def is_valid_dns_label(label: str | None) -> bool:
    """Public wrapper for DNS_LABEL_RE, for callers outside this module's own
    build_app_hostname sink — the RabbitMQ consumers and repositories that first receive a
    dns_label off the wire (see api/messaging/consumers/infrastructure.py and
    api/repositories/infrastructure.py) validate at that boundary too, not only here."""
    return bool(label) and bool(DNS_LABEL_RE.fullmatch(label))

# A single DNS label: lowercase alnum/hyphen, 1-63 chars, no leading/trailing hyphen.
# Narrower than aws/container_config.py's app-hostname validator (a multi-label hostname
# in general) because a host-mode slug must be exactly ONE label under the wildcard
# certificate `*.{dns_label}.{base}` — a slug containing '.' (app_slug allows it, for
# Docker tags) would add a label the wildcard does not cover.
_SLUG_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

# The final, fully-composed-string check — the same shape container_config.py's own
# _HOSTNAME_RE enforces at its own interpolation sink. Belt and braces: even if the two
# checks above somehow both passed something unexpected, this still has to match before
# anything downstream is handed the result.
_HOSTNAME_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)


class HostUrlNotAvailable(ValueError):
    """Raised by build_app_hostname with a stable `reason` string safe to surface to a
    customer as `host_url_status` — never the underlying exception message."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def platform_base_domain() -> str | None:
    return getattr(settings, "PLATFORM_BASE_DOMAIN", None) or None


def _slug_for_host_mode(app_slug: str) -> str | None:
    if _SLUG_LABEL_RE.fullmatch(app_slug):
        return app_slug
    return None


def build_app_hostname(dns_label: str | None, app_slug: str) -> str:
    """Raises HostUrlNotAvailable for every way this can fail to produce a live,
    resolvable hostname. Never returns a value that hasn't passed every check below."""
    base_domain = platform_base_domain()
    if not base_domain:
        raise HostUrlNotAvailable("platform_domain_unconfigured")
    if not is_valid_dns_label(dns_label):
        raise HostUrlNotAvailable("dns_label_not_minted")
    safe_slug = _slug_for_host_mode(app_slug)
    if safe_slug is None:
        raise HostUrlNotAvailable("slug_not_hostname_safe")
    hostname = f"{safe_slug}.{dns_label}.{base_domain}"
    if not _HOSTNAME_RE.fullmatch(hostname):
        raise HostUrlNotAvailable("slug_not_hostname_safe")
    return hostname


def app_host_url(application) -> tuple[str | None, str | None]:
    """The full host_url gate for the application detail API (see views/application.py):
    every infra-level leg (infra_host_ready) plus the app's OWN routing evidence — a
    live infra with TLS/DNS ready is not enough on its own, since this specific app must
    have actually been deployed with the host-mode route applied (see
    application_deployment_service.py:_configure_host_routing and
    api/k8s/deployer.py:EKSDeployer for how host_forward_rule_arn / host_route_applied get
    set). Returns (host_url, None) when live, or (None, reason) otherwise — never raises.
    """
    from shared.enums.orchestrator import ComputeType

    # R2: an app that is BUILDING/DEPLOYING/FAILED/SLEEPING is not serving traffic right
    # now even if a previous deploy's host_forward_rule_arn (ECS) or host_route_applied
    # (EKS) is still recorded — those fields describe the last successful deploy's routing
    # shape, not whether the app is currently reachable. A path-mode deployment_url has the
    # same real-world caveat but is a plain, no-claim-of-freshness field; host_url is
    # advertised as a live, working URL, so it holds to a stricter bar.
    if application.status != 'ACTIVE':
        return None, f"application_not_active_{application.status.lower()}"

    infra = application.infrastructure
    ready, reason = infra_host_ready(infra)
    if not ready:
        return None, reason

    is_eks = infra.compute_type == ComputeType.EKS
    route_applied = application.host_route_applied if is_eks else bool(application.host_forward_rule_arn)
    if not route_applied:
        return None, "host_route_not_applied"

    try:
        hostname = build_app_hostname(infra.dns_label, _app_slug(application.name))
    except HostUrlNotAvailable as exc:
        return None, exc.reason

    return f"https://{hostname}", None


def _app_slug(name: str) -> str:
    from api.common.naming import app_slug
    return app_slug(name)


def infra_host_ready(infrastructure) -> tuple[bool, str | None]:
    """The infra-level legs of the host-URL gate: platform domain configured, dns_label
    minted, certificate ISSUED, DNS confirmed INSYNC (both edge+wildcard), and the 443
    listener/Ingress-TLS patch applied. Deliberately does not check per-app routing
    evidence — callers need different app-level signals for that (whether a deploy should
    *attempt* host mode vs. whether one already has — see application_deployment_service.py
    and views/application.py)."""
    if not platform_base_domain():
        return False, "platform_domain_unconfigured"
    if not infrastructure.dns_label:
        return False, "dns_label_not_minted"
    if infrastructure.tls_status != "ISSUED":
        return False, "tls_not_issued"
    if not infrastructure.dns_synced:
        return False, "dns_not_synced"
    if not infrastructure.https_ready:
        return False, "https_listener_not_applied"
    return True, None
