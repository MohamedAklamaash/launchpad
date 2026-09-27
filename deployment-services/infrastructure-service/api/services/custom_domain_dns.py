"""Authoritative DNS TXT lookup for custom-domain ownership proof (F1b part 3b).

Ownership of a customer-supplied hostname is proven by a TXT record at
`_launchpad-challenge.{hostname}` holding the per-claim token issued by
`CustomDomain.issue_ownership_token()`. This module fetches that record directly from the
hostname's own authoritative nameservers — never a recursive resolver (a customer's DNS
provider, a stub resolver, or any cache in between) — because a recursive answer can be
poisoned or served stale by an intermediary this platform does not control, while the
authoritative servers are the one place the actual zone owner's data lives.

Algorithm: walk from the full hostname up through each parent label until an NS record is
found (that is the zone cut), resolve those nameservers to IPs, then query each directly
with RD=0 ("recursion desired" unset) for the TXT record. No CNAME chasing — an
authoritative answer is taken exactly as returned; a hostname that CNAMEs its challenge
label away from a TXT-bearing name simply produces no answer, which is a verification
failure, not something this module follows.
"""
import logging

import dns.exception
import dns.flags
import dns.message
import dns.query
import dns.rdatatype
import dns.resolver

logger = logging.getLogger(__name__)

CHALLENGE_LABEL = "_launchpad-challenge"

_NS_LOOKUP_TIMEOUT_SECONDS = 5
_QUERY_TIMEOUT_SECONDS = 5


class AuthoritativeLookupError(RuntimeError):
    """The authoritative nameservers for a hostname could not be found, resolved, or
    reached. Distinct from "no TXT record present" (a return of `[]`, not an exception) —
    this means the lookup itself failed, not that ownership proof is absent."""


class MockRealMismatch(RuntimeError):
    """Mirrors route53_client.MockRealMismatch / cert_bootstrap's _acm_client gate: a
    mock/real DNS-resolver mismatch is exactly the kind of bug that would otherwise
    silently accept a real customer's unverified claim in dev mode, or hang a real
    verification on the in-memory fake."""


def _zone_nameservers(hostname: str) -> list[str]:
    labels = hostname.split(".")
    last_exc = None
    for i in range(len(labels) - 1):
        zone = ".".join(labels[i:])
        try:
            answer = dns.resolver.resolve(zone, "NS", lifetime=_NS_LOOKUP_TIMEOUT_SECONDS)
            return sorted({str(r.target).rstrip(".") for r in answer})
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            continue
        except dns.exception.DNSException as exc:
            last_exc = exc
            continue
    raise AuthoritativeLookupError(
        f"no NS records found walking up from {hostname!r}" + (f" ({last_exc})" if last_exc else "")
    )


def _resolve_nameserver_ips(ns_hosts: list[str]) -> list[str]:
    ips: list[str] = []
    for host in ns_hosts:
        for rtype in ("A", "AAAA"):
            try:
                answer = dns.resolver.resolve(host, rtype, lifetime=_NS_LOOKUP_TIMEOUT_SECONDS)
                ips.extend(str(r) for r in answer)
            except dns.exception.DNSException:
                continue
    return ips


def _fetch_authoritative_txt_real(hostname: str) -> list[str]:
    query_name = f"{CHALLENGE_LABEL}.{hostname}"
    ns_hosts = _zone_nameservers(hostname)
    ips = _resolve_nameserver_ips(ns_hosts)
    if not ips:
        raise AuthoritativeLookupError(f"could not resolve any authoritative nameserver IP for {hostname!r}")

    query = dns.message.make_query(query_name, dns.rdatatype.TXT)
    query.flags &= ~dns.flags.RD  # authoritative-only: no recursion desired

    last_exc = None
    for ip in ips:
        try:
            response = dns.query.udp(query, ip, timeout=_QUERY_TIMEOUT_SECONDS)
        except dns.exception.DNSException as exc:
            last_exc = exc
            continue
        values = []
        for rrset in response.answer:
            if rrset.rdtype == dns.rdatatype.TXT:
                for item in rrset:
                    values.append(b"".join(item.strings).decode("utf-8", errors="replace"))
        return values
    raise AuthoritativeLookupError(
        f"no authoritative nameserver answered for {query_name!r} ({last_exc})"
    )


def fetch_authoritative_txt_records(hostname: str, *, infra_is_mock: bool, dev_mode: bool) -> list[str]:
    """Returns the TXT record values at `_launchpad-challenge.{hostname}` as reported by
    the hostname's own authoritative nameservers. Raises AuthoritativeLookupError if the
    lookup itself fails (no NS found, no nameserver reachable) — callers must treat that
    as "verification failed", never as "verification skipped"."""
    if infra_is_mock and not dev_mode:
        raise MockRealMismatch("refusing authoritative DNS lookup for a mock infrastructure outside dev mode")
    if dev_mode and not infra_is_mock:
        raise MockRealMismatch("refusing a real authoritative DNS lookup against a mock infrastructure check inside dev mode")

    if dev_mode:
        return _shared_fake_resolver().get_txt(hostname)
    return _fetch_authoritative_txt_real(hostname)


def verify_ownership_token(domain, *, infra_is_mock: bool, dev_mode: bool) -> bool:
    """True iff one of the hostname's authoritative TXT records at
    `_launchpad-challenge.{hostname}` matches `domain`'s ownership token (constant-time
    compare against the stored hash — see CustomDomain.check_ownership_token). A lookup
    failure (no NS, no reachable nameserver) is a verification failure, not an error the
    caller needs to distinguish — an attacker able to make lookups fail transiently gains
    nothing from that being reported differently than "wrong token"."""
    try:
        values = fetch_authoritative_txt_records(domain.hostname, infra_is_mock=infra_is_mock, dev_mode=dev_mode)
    except AuthoritativeLookupError:
        logger.warning("authoritative TXT lookup failed for %r", domain.hostname, exc_info=True)
        return False
    return any(domain.check_ownership_token(value) for value in values)


# ── mock DNS double ──────────────────────────────────────────────────────────────

_fake_resolver_singleton = None


def _shared_fake_resolver():
    global _fake_resolver_singleton
    if _fake_resolver_singleton is None:
        _fake_resolver_singleton = FakeAuthoritativeResolver()
    return _fake_resolver_singleton


def reset_fake_resolver_for_tests() -> None:
    global _fake_resolver_singleton
    _fake_resolver_singleton = None


class FakeAuthoritativeResolver:
    """In-memory double standing in for "the customer's real DNS provider", for
    is_mock/dev verification and tests — mirrors route53_client.FakeRoute53Zone and
    cert_bootstrap.FakeAcmClient: process-local, just enough surface for this module.
    A hostname with no record set answers with an empty list (a real NXDOMAIN/NOERROR-
    no-data authoritative answer), not an error — only an unreachable/misconfigured zone
    raises AuthoritativeLookupError, and the fake has no such failure mode to simulate."""

    def __init__(self):
        self._records: dict[str, list[str]] = {}

    def set_txt(self, hostname: str, value: str) -> None:
        self._records.setdefault(hostname, [])
        if value not in self._records[hostname]:
            self._records[hostname].append(value)

    def clear_txt(self, hostname: str) -> None:
        self._records.pop(hostname, None)

    def get_txt(self, hostname: str) -> list[str]:
        return list(self._records.get(hostname, []))
