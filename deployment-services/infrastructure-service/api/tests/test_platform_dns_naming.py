"""Name/value validation the writer runs before any Route53 call — F1b part 1 pre-review §1.

Worst case per the pre-review: a CNAME inside the tenant's own label. These tests assert
the narrow allow-list shape, not a denylist: anything that isn't exactly the expected shape
is refused, not merely "obviously bad" input.
"""
import pytest
from api.services.platform_dns import naming

LABEL = "0123456789abcdef"
OTHER_LABEL = "fedcba9876543210"
BASE = "launchpad.app"


def test_edge_and_wildcard_record_names():
    assert naming.edge_record_name(LABEL, BASE) == f"edge.{LABEL}.{BASE}"
    assert naming.wildcard_record_name(LABEL, BASE) == f"*.{LABEL}.{BASE}"


@pytest.mark.parametrize("bad_label", ["", "short", "0123456789abcdeg", "0123456789ABCDEF", "../../etc"])
def test_rejects_invalid_dns_label(bad_label):
    with pytest.raises(naming.InvalidDnsRecordError):
        naming.validate_dns_label(bad_label)


def test_assert_owned_record_name_accepts_own_edge():
    naming.assert_owned_record_name(f"edge.{LABEL}.{BASE}", LABEL, BASE, kind="edge")


def test_assert_owned_record_name_rejects_other_labels_edge():
    with pytest.raises(naming.InvalidDnsRecordError):
        naming.assert_owned_record_name(f"edge.{OTHER_LABEL}.{BASE}", LABEL, BASE, kind="edge")


def test_assert_owned_record_name_rejects_apex_and_single_label():
    with pytest.raises(naming.InvalidDnsRecordError):
        naming.assert_owned_record_name(BASE, LABEL, BASE, kind="edge")


@pytest.mark.parametrize("hostile_name", [
    f"evil.{LABEL}.{BASE}",             # not the exact edge leaf
    f"edge.{LABEL}.{BASE}.evil.com",    # suffix trick
    f"edge.{OTHER_LABEL}.{BASE}",       # another tenant's label — the cross-tenant case
    BASE,                                # apex
    f"www.{BASE}",                       # single label under apex
])
def test_edge_rejects_every_hostile_name(hostile_name):
    with pytest.raises(naming.InvalidDnsRecordError):
        naming.assert_owned_record_name(hostile_name, LABEL, BASE, kind="edge")


def test_validation_leaf_accepts_acm_shape():
    name = f"_a79865eb4cd1a6ab990a45779c92cf6f.{LABEL}.{BASE}"
    naming.assert_owned_record_name(name, LABEL, BASE, kind="validation")


@pytest.mark.parametrize("hostile_validation_name", [
    f"_a79865eb4cd1a6ab990a45779c92cf6f.{OTHER_LABEL}.{BASE}",  # another tenant's label
    f"not-underscore-prefixed.{LABEL}.{BASE}",
    f"_{LABEL}.{BASE}",  # one label short — not under this label at all
    BASE,
])
def test_validation_rejects_hostile_names(hostile_validation_name):
    with pytest.raises(naming.InvalidDnsRecordError):
        naming.assert_owned_record_name(hostile_validation_name, LABEL, BASE, kind="validation")


REGION = "us-east-1"


@pytest.mark.parametrize("value", [
    f"internal-abc123.{REGION}.elb.amazonaws.com",
    f"dualstack.internal-abc123.{REGION}.elb.amazonaws.com",
])
def test_edge_target_accepts_elb_hostname_in_region(value):
    naming.assert_valid_edge_target(value, REGION)


@pytest.mark.parametrize("hostile_value", [
    "attacker-bucket.s3.amazonaws.com",              # claimable S3, not an ELB at all
    "internal-abc123.us-west-2.elb.amazonaws.com",   # right shape, wrong region
    "internal-abc123.us-east-1.elb.amazonaws.com.evil.com",
    "not-an-elb-hostname",
    "",
])
def test_edge_target_rejects_hostile_values(hostile_value):
    with pytest.raises(naming.InvalidDnsRecordError):
        naming.assert_valid_edge_target(hostile_value, REGION)


def test_validation_value_requires_acm_suffix():
    naming.assert_valid_validation_value("_a79865eb4cd1a6ab990a45779c92cf6f.xlfgrmvvlj.acm-validations.aws.")
    with pytest.raises(naming.InvalidDnsRecordError):
        naming.assert_valid_validation_value("abc123.acm-validations.aws")  # no trailing dot
    with pytest.raises(naming.InvalidDnsRecordError):
        naming.assert_valid_validation_value("attacker.controlled.value.")


def test_two_labels_below_apex_backstop():
    naming.assert_two_labels_below_apex(f"edge.{LABEL}.{BASE}", BASE)
    with pytest.raises(naming.InvalidDnsRecordError):
        naming.assert_two_labels_below_apex(BASE, BASE)
    with pytest.raises(naming.InvalidDnsRecordError):
        naming.assert_two_labels_below_apex(f"single.{BASE}", BASE)


def test_route53_name_normalization_round_trips_wildcard():
    wire_form = r"\052.0123456789abcdef.launchpad.app."
    canonical = naming.normalize_route53_name(wire_form)
    assert canonical == "*.0123456789abcdef.launchpad.app"
    assert naming.denormalize_for_route53(canonical) == wire_form
