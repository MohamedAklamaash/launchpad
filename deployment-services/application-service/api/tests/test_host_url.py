"""api/common/host_url.py — hostname construction/validation and the infra-level host-URL
gate (F1b part 3a)."""
from types import SimpleNamespace

import pytest

from api.common.host_url import (
    HostUrlNotAvailable,
    build_app_hostname,
    infra_host_ready,
)


@pytest.fixture(autouse=True)
def platform_domain(settings):
    settings.PLATFORM_BASE_DOMAIN = "launchpad.aklamaash.me"


def test_build_app_hostname_happy_path():
    assert build_app_hostname("0123456789abcdef", "myapp") == "myapp.0123456789abcdef.launchpad.aklamaash.me"


def test_build_app_hostname_rejects_unconfigured_platform_domain(settings):
    settings.PLATFORM_BASE_DOMAIN = None
    with pytest.raises(HostUrlNotAvailable) as exc:
        build_app_hostname("0123456789abcdef", "myapp")
    assert exc.value.reason == "platform_domain_unconfigured"


@pytest.mark.parametrize("label", [None, "", "short", "0123456789abcdef1", "ZZZZZZZZZZZZZZZZ", "../../etc"])
def test_build_app_hostname_rejects_malshaped_dns_label(label):
    with pytest.raises(HostUrlNotAvailable) as exc:
        build_app_hostname(label, "myapp")
    assert exc.value.reason == "dns_label_not_minted"


@pytest.mark.parametrize("slug", ["", "my.app", "my_app", "-leading-hyphen", "trailing-hyphen-", "a" * 64])
def test_build_app_hostname_rejects_slugs_that_are_not_a_single_dns_label(slug):
    with pytest.raises(HostUrlNotAvailable) as exc:
        build_app_hostname("0123456789abcdef", slug)
    assert exc.value.reason == "slug_not_hostname_safe"


def test_build_app_hostname_rejects_config_injection_via_dns_label():
    """A dns_label carrying a config-injection payload (a hostile value that somehow reached
    this far) must never be interpolated — this is the last check before any consumer
    treats the string as a hostname."""
    hostile = "x" * 8 + "\r\nSet: evil"
    with pytest.raises(HostUrlNotAvailable):
        build_app_hostname(hostile, "myapp")


# ── infra_host_ready ─────────────────────────────────────────────────────────────────────

def _infra(**overrides):
    defaults = {"dns_label": "0123456789abcdef", "tls_status": "ISSUED", "dns_synced": True, "https_ready": True}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_infra_host_ready_true_when_every_leg_is_satisfied():
    assert infra_host_ready(_infra()) == (True, None)


def test_infra_host_ready_false_without_platform_domain(settings):
    settings.PLATFORM_BASE_DOMAIN = None
    assert infra_host_ready(_infra()) == (False, "platform_domain_unconfigured")


def test_infra_host_ready_false_without_dns_label():
    assert infra_host_ready(_infra(dns_label=None)) == (False, "dns_label_not_minted")


def test_infra_host_ready_false_when_tls_not_issued():
    assert infra_host_ready(_infra(tls_status="PENDING")) == (False, "tls_not_issued")


def test_infra_host_ready_false_when_dns_not_synced():
    assert infra_host_ready(_infra(dns_synced=False)) == (False, "dns_not_synced")


def test_infra_host_ready_false_when_https_not_applied():
    assert infra_host_ready(_infra(https_ready=False)) == (False, "https_listener_not_applied")
