"""Authoritative TXT lookup for custom-domain ownership proof: real-path plumbing against
mocked dnspython NS/A/AAAA resolution and real dnspython Message objects for the TXT
response (so AA-flag and CNAME-chain handling is exercised against the actual library
behavior, not a hand-rolled mock of it) — never a live network query in tests. Covers the
mock/dev fake resolver, the is_mock/dev_mode fail-closed gate, and the security-review
hardening: a single shared per-lookup deadline, label/host/IP caps, non-global IPs
(loopback, RFC1918, link-local/metadata) never queried, and OSError treated the same as a
DNS-level failure."""
import types
from unittest.mock import patch

import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.rdatatype
import dns.resolver
import dns.rrset
import pytest
from api.services import custom_domain_dns


@pytest.fixture(autouse=True)
def _reset_fake_resolver():
    custom_domain_dns.reset_fake_resolver_for_tests()
    yield
    custom_domain_dns.reset_fake_resolver_for_tests()


def _ns_answer(names):
    return [types.SimpleNamespace(target=types.SimpleNamespace(__str__=lambda self, n=n: f"{n}.")) for n in names]


def _response(qname_text, *, aa=True, txt_values=None, cname_target=None):
    """A real dns.message.Message, built the way dnspython itself expects (index reset
    after manual rrset appends — see canonical_name()/resolve_chaining()'s reliance on
    Message.find_rrset, which otherwise silently misses rrsets added by simple list
    append)."""
    qname = dns.name.from_text(qname_text)
    query = dns.message.make_query(qname, dns.rdatatype.TXT)
    response = dns.message.make_response(query)
    if aa:
        response.flags |= dns.flags.AA
    if cname_target:
        response.answer.append(dns.rrset.from_text(str(qname), 300, "IN", "CNAME", cname_target))
        if txt_values:
            target = cname_target if cname_target.endswith(".") else cname_target + "."
            response.answer.append(
                dns.rrset.from_text(target, 300, "IN", "TXT", *[f'"{v}"' for v in txt_values])
            )
    elif txt_values:
        response.answer.append(
            dns.rrset.from_text(str(qname), 300, "IN", "TXT", *[f'"{v}"' for v in txt_values])
        )
    response.index = None
    return response


def test_fetch_authoritative_txt_real_walks_up_for_ns_and_queries_each_ip():
    def fake_resolve(name, rtype, lifetime=None):
        if rtype == "NS":
            if name == "app.example.com":
                raise dns.resolver.NoAnswer()
            if name == "example.com":
                return _ns_answer(["ns1.example.com"])
            raise dns.resolver.NXDOMAIN()
        if rtype == "A":
            return ["1.2.3.10"]
        if rtype == "AAAA":
            raise dns.resolver.NoAnswer()
        raise AssertionError("unexpected rtype")

    response = _response("_launchpad-challenge.app.example.com", txt_values=["hello-world-token"])

    with patch("dns.resolver.resolve", side_effect=fake_resolve), \
         patch("dns.query.udp", return_value=response) as udp_mock:
        values = custom_domain_dns.fetch_authoritative_txt_records(
            "app.example.com", infra_is_mock=False, dev_mode=False,
        )

    assert values == ["hello-world-token"]
    called_ip = udp_mock.call_args.args[1]
    assert called_ip == "1.2.3.10"
    sent_query = udp_mock.call_args.args[0]
    assert sent_query.question[0].name.to_text().rstrip(".") == "_launchpad-challenge.app.example.com"
    assert not (sent_query.flags & dns.flags.RD)


def test_fetch_authoritative_txt_real_raises_when_no_ns_found():
    with patch("dns.resolver.resolve", side_effect=dns.resolver.NXDOMAIN()), \
         pytest.raises(custom_domain_dns.AuthoritativeLookupError):
        custom_domain_dns.fetch_authoritative_txt_records(
            "app.example.com", infra_is_mock=False, dev_mode=False,
        )


def test_fetch_authoritative_txt_real_raises_when_no_nameserver_reachable():
    def fake_resolve(name, rtype, lifetime=None):
        if rtype == "NS":
            return _ns_answer(["ns1.example.com"])
        raise dns.exception.Timeout()

    with patch("dns.resolver.resolve", side_effect=fake_resolve), \
         pytest.raises(custom_domain_dns.AuthoritativeLookupError):
        custom_domain_dns.fetch_authoritative_txt_records(
            "app.example.com", infra_is_mock=False, dev_mode=False,
        )


def test_fetch_authoritative_txt_real_raises_when_every_query_times_out():
    def fake_resolve(name, rtype, lifetime=None):
        if rtype == "NS":
            return _ns_answer(["ns1.example.com"])
        if rtype == "A":
            return ["1.2.3.10"]
        raise dns.resolver.NoAnswer()

    with patch("dns.resolver.resolve", side_effect=fake_resolve), \
         patch("dns.query.udp", side_effect=dns.exception.Timeout()), \
         pytest.raises(custom_domain_dns.AuthoritativeLookupError):
        custom_domain_dns.fetch_authoritative_txt_records(
            "app.example.com", infra_is_mock=False, dev_mode=False,
        )


def test_fetch_authoritative_txt_real_raises_on_os_error_not_just_dns_exception():
    """dns.query.udp can raise a raw socket OSError (connection refused, network
    unreachable) that is not a dns.exception.DNSException subclass — a bare except on
    the DNS-specific hierarchy would let this escape as an unhandled exception instead
    of a clean AuthoritativeLookupError."""
    def fake_resolve(name, rtype, lifetime=None):
        if rtype == "NS":
            return _ns_answer(["ns1.example.com"])
        if rtype == "A":
            return ["1.2.3.10"]
        raise dns.resolver.NoAnswer()

    with patch("dns.resolver.resolve", side_effect=fake_resolve), \
         patch("dns.query.udp", side_effect=ConnectionRefusedError("boom")), \
         pytest.raises(custom_domain_dns.AuthoritativeLookupError):
        custom_domain_dns.fetch_authoritative_txt_records(
            "app.example.com", infra_is_mock=False, dev_mode=False,
        )


@pytest.mark.parametrize("bad_ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1"])
def test_non_global_nameserver_ips_are_never_queried(bad_ip):
    """169.254.169.254 is the cloud metadata address — a customer-controlled 'nameserver
    IP' is otherwise a live SSRF/timing-oracle primitive against this platform's own VPC,
    not just a loopback curiosity."""
    def fake_resolve(name, rtype, lifetime=None):
        if rtype == "NS":
            return _ns_answer(["ns1.example.com"])
        if rtype == "A":
            return [bad_ip] if ":" not in bad_ip else []
        if rtype == "AAAA":
            return [bad_ip] if ":" in bad_ip else []
        raise dns.resolver.NoAnswer()

    with patch("dns.resolver.resolve", side_effect=fake_resolve), \
         patch("dns.query.udp") as udp_mock, \
         pytest.raises(custom_domain_dns.AuthoritativeLookupError):
        custom_domain_dns.fetch_authoritative_txt_records(
            "app.example.com", infra_is_mock=False, dev_mode=False,
        )
    udp_mock.assert_not_called()


def test_global_ip_alongside_a_non_global_one_is_still_queried():
    def fake_resolve(name, rtype, lifetime=None):
        if rtype == "NS":
            return _ns_answer(["ns1.example.com"])
        if rtype == "A":
            return ["10.0.0.1", "1.2.3.10"]
        if rtype == "AAAA":
            raise dns.resolver.NoAnswer()
        raise dns.resolver.NoAnswer()

    response = _response("_launchpad-challenge.app.example.com", txt_values=["ok"])

    with patch("dns.resolver.resolve", side_effect=fake_resolve), \
         patch("dns.query.udp", return_value=response) as udp_mock:
        values = custom_domain_dns.fetch_authoritative_txt_records(
            "app.example.com", infra_is_mock=False, dev_mode=False,
        )

    assert values == ["ok"]
    assert udp_mock.call_args.args[1] == "1.2.3.10"


def test_ns_host_count_is_capped():
    hosts = [f"ns{i}.example.com" for i in range(20)]
    resolved_ns_query_count = {"count": 0}

    def fake_resolve(name, rtype, lifetime=None):
        if rtype == "NS":
            return _ns_answer(hosts)
        if rtype in ("A", "AAAA"):
            resolved_ns_query_count["count"] += 1
            raise dns.resolver.NoAnswer()
        raise AssertionError

    with patch("dns.resolver.resolve", side_effect=fake_resolve), \
         pytest.raises(custom_domain_dns.AuthoritativeLookupError):
        custom_domain_dns.fetch_authoritative_txt_records(
            "app.example.com", infra_is_mock=False, dev_mode=False,
        )

    # 2 rtypes (A, AAAA) per NS host, capped at _MAX_NS_HOSTS hosts.
    assert resolved_ns_query_count["count"] == custom_domain_dns._MAX_NS_HOSTS * 2


def test_ns_ip_count_is_capped():
    def fake_resolve(name, rtype, lifetime=None):
        if rtype == "NS":
            return _ns_answer(["ns1.example.com"])
        if rtype == "A":
            return [f"1.2.3.{i}" for i in range(1, 30)]
        if rtype == "AAAA":
            raise dns.resolver.NoAnswer()
        raise AssertionError

    attempted_ips = []

    def fake_udp(query, ip, timeout=None):
        attempted_ips.append(ip)
        raise dns.exception.Timeout()

    with patch("dns.resolver.resolve", side_effect=fake_resolve), \
         patch("dns.query.udp", side_effect=fake_udp), \
         pytest.raises(custom_domain_dns.AuthoritativeLookupError):
        custom_domain_dns.fetch_authoritative_txt_records(
            "app.example.com", infra_is_mock=False, dev_mode=False,
        )

    assert len(attempted_ips) == custom_domain_dns._MAX_NS_IPS


def test_label_walk_is_capped():
    deep_hostname = ".".join(f"l{i}" for i in range(20)) + ".example.com"
    ns_query_names = []

    def fake_resolve(name, rtype, lifetime=None):
        if rtype == "NS":
            ns_query_names.append(name)
            raise dns.resolver.NXDOMAIN()
        raise AssertionError

    with patch("dns.resolver.resolve", side_effect=fake_resolve), \
         pytest.raises(custom_domain_dns.AuthoritativeLookupError):
        custom_domain_dns.fetch_authoritative_txt_records(
            deep_hostname, infra_is_mock=False, dev_mode=False,
        )

    assert len(ns_query_names) == custom_domain_dns._MAX_LABELS_TO_WALK


def test_shared_deadline_is_exhausted_by_earlier_calls():
    """A per-call timeout alone would let a hostname with many slow/unreachable
    nameservers multiply out an unbounded total wall-clock cost — the deadline is shared
    across the whole lookup, not reset per call."""
    call_times = [0.0]

    def fake_monotonic():
        return call_times[0]

    def fake_resolve(name, rtype, lifetime=None):
        if rtype == "NS":
            call_times[0] += custom_domain_dns._TOTAL_LOOKUP_DEADLINE_SECONDS + 1
            return _ns_answer(["ns1.example.com"])
        raise AssertionError("must not attempt IP resolution once the deadline is exhausted")

    with patch("time.monotonic", side_effect=fake_monotonic), \
         patch("dns.resolver.resolve", side_effect=fake_resolve), \
         pytest.raises(custom_domain_dns.AuthoritativeLookupError):
        custom_domain_dns.fetch_authoritative_txt_records(
            "app.example.com", infra_is_mock=False, dev_mode=False,
        )


def test_response_missing_aa_flag_is_treated_as_no_answer():
    def fake_resolve(name, rtype, lifetime=None):
        if rtype == "NS":
            return _ns_answer(["ns1.example.com"])
        if rtype == "A":
            return ["1.2.3.10"]
        raise dns.resolver.NoAnswer()

    response = _response("_launchpad-challenge.app.example.com", aa=False, txt_values=["not-authoritative"])

    with patch("dns.resolver.resolve", side_effect=fake_resolve), \
         patch("dns.query.udp", return_value=response):
        values = custom_domain_dns.fetch_authoritative_txt_records(
            "app.example.com", infra_is_mock=False, dev_mode=False,
        )

    assert values == []


def test_cname_redirected_answer_is_not_followed():
    """The module's stated policy is no CNAME chasing — a server redirecting the
    challenge label elsewhere must not have that target's TXT silently accepted, even if
    the same response carries both records."""
    def fake_resolve(name, rtype, lifetime=None):
        if rtype == "NS":
            return _ns_answer(["ns1.example.com"])
        if rtype == "A":
            return ["1.2.3.10"]
        raise dns.resolver.NoAnswer()

    response = _response(
        "_launchpad-challenge.app.example.com", cname_target="attacker.example.net.", txt_values=["sneaky"],
    )

    with patch("dns.resolver.resolve", side_effect=fake_resolve), \
         patch("dns.query.udp", return_value=response):
        values = custom_domain_dns.fetch_authoritative_txt_records(
            "app.example.com", infra_is_mock=False, dev_mode=False,
        )

    assert values == []


def test_dev_mode_uses_fake_resolver_not_real_dns():
    custom_domain_dns._shared_fake_resolver().set_txt("app.example.com", "abc123")

    with patch("dns.resolver.resolve", side_effect=AssertionError("must not touch real DNS")):
        values = custom_domain_dns.fetch_authoritative_txt_records(
            "app.example.com", infra_is_mock=True, dev_mode=True,
        )

    assert values == ["abc123"]


def test_fake_resolver_returns_empty_list_for_unset_hostname():
    values = custom_domain_dns.fetch_authoritative_txt_records(
        "unset.example.com", infra_is_mock=True, dev_mode=True,
    )
    assert values == []


@pytest.mark.parametrize("infra_is_mock,dev_mode", [(True, False), (False, True)])
def test_mock_real_mismatch_raises(infra_is_mock, dev_mode):
    with pytest.raises(custom_domain_dns.MockRealMismatch):
        custom_domain_dns.fetch_authoritative_txt_records(
            "app.example.com", infra_is_mock=infra_is_mock, dev_mode=dev_mode,
        )


class _FakeDomain:
    def __init__(self, hostname, token):
        self.hostname = hostname
        self._token = token

    def check_ownership_token(self, candidate):
        return candidate == self._token


def test_verify_ownership_token_true_on_matching_txt():
    custom_domain_dns._shared_fake_resolver().set_txt("app.example.com", "the-real-token")
    domain = _FakeDomain("app.example.com", "the-real-token")

    assert custom_domain_dns.verify_ownership_token(domain, infra_is_mock=True, dev_mode=True) is True


def test_verify_ownership_token_false_on_wrong_txt():
    custom_domain_dns._shared_fake_resolver().set_txt("app.example.com", "someone-elses-token")
    domain = _FakeDomain("app.example.com", "the-real-token")

    assert custom_domain_dns.verify_ownership_token(domain, infra_is_mock=True, dev_mode=True) is False


def test_verify_ownership_token_false_when_no_record_published():
    domain = _FakeDomain("app.example.com", "the-real-token")

    assert custom_domain_dns.verify_ownership_token(domain, infra_is_mock=True, dev_mode=True) is False


def test_verify_ownership_token_false_on_lookup_failure_not_exception():
    domain = _FakeDomain("app.example.com", "the-real-token")

    with patch("dns.resolver.resolve", side_effect=dns.resolver.NXDOMAIN()):
        assert custom_domain_dns.verify_ownership_token(domain, infra_is_mock=False, dev_mode=False) is False
