"""Authoritative TXT lookup for custom-domain ownership proof: real-path plumbing against
mocked dnspython calls (never a live network query in tests), the mock/dev fake resolver,
and the is_mock/dev_mode fail-closed gate shared with cert_bootstrap/route53_client."""
import types
from unittest.mock import MagicMock, patch

import dns.exception
import dns.rdatatype
import pytest
from api.services import custom_domain_dns


@pytest.fixture(autouse=True)
def _reset_fake_resolver():
    custom_domain_dns.reset_fake_resolver_for_tests()
    yield
    custom_domain_dns.reset_fake_resolver_for_tests()


def _ns_answer(names):
    return [types.SimpleNamespace(target=types.SimpleNamespace(__str__=lambda self, n=n: f"{n}.")) for n in names]


def _txt_rrset(values):
    rrset = MagicMock()
    rrset.rdtype = dns.rdatatype.TXT
    rrset.__iter__.return_value = iter([
        types.SimpleNamespace(strings=[v.encode()]) for v in values
    ])
    return rrset


class _FakeResolverAnswer(list):
    pass


def test_fetch_authoritative_txt_real_walks_up_for_ns_and_queries_each_ip():
    def fake_resolve(name, rtype, lifetime=None):
        if rtype == "NS":
            if name == "app.example.com":
                raise dns.resolver.NoAnswer()
            if name == "example.com":
                return _ns_answer(["ns1.example.com"])
            raise dns.resolver.NXDOMAIN()
        if rtype == "A":
            return ["203.0.113.10"]
        if rtype == "AAAA":
            raise dns.resolver.NoAnswer()
        raise AssertionError("unexpected rtype")

    fake_response = MagicMock()
    fake_response.answer = [_txt_rrset(["hello-world-token"])]

    with patch("dns.resolver.resolve", side_effect=fake_resolve), \
         patch("dns.query.udp", return_value=fake_response) as udp_mock:
        values = custom_domain_dns.fetch_authoritative_txt_records(
            "app.example.com", infra_is_mock=False, dev_mode=False,
        )

    assert values == ["hello-world-token"]
    called_ip = udp_mock.call_args.args[1]
    assert called_ip == "203.0.113.10"
    sent_query = udp_mock.call_args.args[0]
    assert sent_query.question[0].name.to_text().rstrip(".") == "_launchpad-challenge.app.example.com"
    assert not (sent_query.flags & __import__("dns.flags", fromlist=["RD"]).RD)


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
            return ["203.0.113.10"]
        raise dns.resolver.NoAnswer()

    with patch("dns.resolver.resolve", side_effect=fake_resolve), \
         patch("dns.query.udp", side_effect=dns.exception.Timeout()), \
         pytest.raises(custom_domain_dns.AuthoritativeLookupError):
        custom_domain_dns.fetch_authoritative_txt_records(
            "app.example.com", infra_is_mock=False, dev_mode=False,
        )


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
