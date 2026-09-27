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
with RD=0 ("recursion desired" unset) for the TXT record, requiring the AA (authoritative
answer) bit and rejecting a CNAME'd answer (see `_extract_txt_values`).

Security/reliability bounds (a claimed hostname's DNS is entirely customer-controlled, so
this whole module is an attacker-reachable surface from a security review's point of view):
this whole lookup — every zone-walk step, every NS resolution, every TXT query, across
every nameserver tried — is bounded by ONE shared wall-clock deadline
(`_TOTAL_LOOKUP_DEADLINE_SECONDS`), not a per-call timeout that a hostname with many slow
nameservers could multiply out arbitrarily. Label/host/IP counts are separately capped
(`_MAX_LABELS_TO_WALK`/`_MAX_NS_HOSTS`/`_MAX_NS_IPS`) so a pathological zone can't force an
unbounded number of network round trips even within the deadline. Every candidate IP is
required to be globally routable (`ipaddress.*.is_global`) before this module ever sends it
a UDP packet — a customer-controlled "nameserver IP" is otherwise a live SSRF/timing-oracle
primitive against this platform's own VPC (loopback, RFC1918, and the cloud metadata
address `169.254.169.254` are all reachable-network addresses from inside a worker process,
not just localhost).
"""
import ipaddress
import logging
import time

import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.query
import dns.rdatatype
import dns.resolver

logger = logging.getLogger(__name__)

CHALLENGE_LABEL = "_launchpad-challenge"

# One shared deadline for an entire lookup (zone walk + NS resolution + every query
# attempt), not a per-call timeout — see the module docstring. Comfortably inside the
# hard per-tick executor timeout the periodic re-validation job wraps this in
# (run_worker.py), which itself is well inside the Redis tick lock's TTL.
_TOTAL_LOOKUP_DEADLINE_SECONDS = 10.0
# Per-call ceiling passed to dnspython — always further clamped to whatever remains of
# the shared deadline above, so no single call can outlast it.
_PER_CALL_TIMEOUT_SECONDS = 3.0

_MAX_LABELS_TO_WALK = 10
_MAX_NS_HOSTS = 4
_MAX_NS_IPS = 8


class AuthoritativeLookupError(RuntimeError):
    """The authoritative nameservers for a hostname could not be found, resolved, or
    reached (including hitting the shared lookup deadline). Distinct from "no TXT record
    present" (a return of `[]`, not an exception) — this means the lookup itself failed,
    not that ownership proof is absent."""


class MockRealMismatch(RuntimeError):
    """Mirrors route53_client.MockRealMismatch / cert_bootstrap's _acm_client gate: a
    mock/real DNS-resolver mismatch is exactly the kind of bug that would otherwise
    silently accept a real customer's unverified claim in dev mode, or hang a real
    verification on the in-memory fake."""


class _Deadline:
    """A shared wall-clock budget threaded through every network call in one lookup.
    `remaining()` raises once exhausted so callers don't need to separately check before
    every call; `per_call()` clamps dnspython's own timeout to whatever's left so a
    single slow server can't consume more than the total budget."""

    def __init__(self, total_seconds: float):
        self._deadline = time.monotonic() + total_seconds

    def remaining(self) -> float:
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise AuthoritativeLookupError("authoritative lookup exceeded its time budget")
        return left

    def per_call(self) -> float:
        return min(_PER_CALL_TIMEOUT_SECONDS, self.remaining())


def _is_global_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def _zone_nameservers(hostname: str, deadline: _Deadline) -> list[str]:
    labels = hostname.split(".")
    walked = 0
    last_exc = None
    for i in range(len(labels) - 1):
        if walked >= _MAX_LABELS_TO_WALK:
            break
        walked += 1
        zone = ".".join(labels[i:])
        try:
            answer = dns.resolver.resolve(zone, "NS", lifetime=deadline.per_call())
            return sorted({str(r.target).rstrip(".") for r in answer})[:_MAX_NS_HOSTS]
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            continue
        except (dns.exception.DNSException, OSError) as exc:
            last_exc = exc
            continue
    raise AuthoritativeLookupError(
        f"no NS records found walking up from {hostname!r}" + (f" ({last_exc})" if last_exc else "")
    )


def _resolve_nameserver_ips(ns_hosts: list[str], deadline: _Deadline) -> list[str]:
    ips: list[str] = []
    for host in ns_hosts:
        for rtype in ("A", "AAAA"):
            if len(ips) >= _MAX_NS_IPS:
                return ips[:_MAX_NS_IPS]
            try:
                answer = dns.resolver.resolve(host, rtype, lifetime=deadline.per_call())
            except (dns.exception.DNSException, OSError):
                continue
            for r in answer:
                ip = str(r)
                if _is_global_ip(ip):
                    ips.append(ip)
                else:
                    logger.warning("dropping non-global authoritative-NS IP %r for %r", ip, host)
                if len(ips) >= _MAX_NS_IPS:
                    break
    return ips[:_MAX_NS_IPS]


def _extract_txt_values(response, query_name: dns.name.Name) -> list[str]:
    """Only a directly-owned, authoritative TXT rrset counts. Two things a naive
    "take any TXT rrset in the answer section" implementation would get wrong:

    1. A response is not authoritative just because we asked with RD=0 — a misconfigured
       or malicious server can still set AA on a non-authoritative or fabricated answer,
       but the AA bit's absence is at least a reliable negative signal, and dnspython
       itself does not otherwise expose "is this genuinely the zone's own answer."
       Requiring AA is the floor this module can check without a DNSSEC chain.
    2. `response.canonical_name(query_name)` follows any CNAME chain *within this
       response* to whatever name the query name ultimately points to. If that canonical
       name differs from the query name itself, the server is redirecting the challenge
       label elsewhere — the module's own stated policy (see the module docstring) is
       "no CNAME chasing," so this is treated as no answer, not silently accepting a TXT
       for a different owner name that happened to ride along in the same message.
    """
    if not (response.flags & dns.flags.AA):
        return []
    try:
        canonical = response.canonical_name()
    except Exception:
        return []
    if canonical != query_name:
        return []

    values = []
    for rrset in response.answer:
        if rrset.rdtype != dns.rdatatype.TXT or rrset.name != query_name:
            continue
        for item in rrset:
            values.append(b"".join(item.strings).decode("utf-8", errors="replace"))
    return values


def _fetch_authoritative_txt_real(hostname: str, deadline: _Deadline) -> list[str]:
    query_name = dns.name.from_text(f"{CHALLENGE_LABEL}.{hostname}")
    ns_hosts = _zone_nameservers(hostname, deadline)
    ips = _resolve_nameserver_ips(ns_hosts, deadline)
    if not ips:
        raise AuthoritativeLookupError(f"could not resolve any authoritative nameserver IP for {hostname!r}")

    query = dns.message.make_query(query_name, dns.rdatatype.TXT)
    query.flags &= ~dns.flags.RD  # authoritative-only: no recursion desired

    last_exc = None
    for ip in ips:
        try:
            response = dns.query.udp(query, ip, timeout=deadline.per_call())
        except (dns.exception.DNSException, OSError) as exc:
            last_exc = exc
            continue
        return _extract_txt_values(response, query_name)
    raise AuthoritativeLookupError(
        f"no authoritative nameserver answered for {query_name!r} ({last_exc})"
    )


def fetch_authoritative_txt_records(hostname: str, *, infra_is_mock: bool, dev_mode: bool) -> list[str]:
    """Returns the TXT record values at `_launchpad-challenge.{hostname}` as reported by
    the hostname's own authoritative nameservers. Raises AuthoritativeLookupError if the
    lookup itself fails (no NS found, no nameserver reachable, or the shared time budget
    is exhausted) — callers must treat that as "verification failed", never as
    "verification skipped"."""
    if infra_is_mock and not dev_mode:
        raise MockRealMismatch("refusing authoritative DNS lookup for a mock infrastructure outside dev mode")
    if dev_mode and not infra_is_mock:
        raise MockRealMismatch("refusing a real authoritative DNS lookup against a mock infrastructure check inside dev mode")

    if dev_mode:
        return _shared_fake_resolver().get_txt(hostname)
    return _fetch_authoritative_txt_real(hostname, _Deadline(_TOTAL_LOOKUP_DEADLINE_SECONDS))


def verify_ownership_token(domain, *, infra_is_mock: bool, dev_mode: bool) -> bool:
    """True iff one of the hostname's authoritative TXT records at
    `_launchpad-challenge.{hostname}` matches `domain`'s ownership token (constant-time
    compare against the stored hash — see CustomDomain.check_ownership_token). A lookup
    failure (no NS, no reachable nameserver, deadline exceeded) is a verification
    failure, not an error the caller needs to distinguish — an attacker able to make
    lookups fail transiently gains nothing from that being reported differently than
    "wrong token"."""
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
