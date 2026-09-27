"""Name and value validation for the platform DNS writer.

Every one of these checks runs before any Route53 call is made — see converge.py. The IAM
policy in infra/platform-dns permits any name two-or-more labels below the zone apex for
every tenant alike (see that module's README); it does not and cannot restrict a write to
one tenant's own label. That restriction is entirely this module's job. Getting one of
these wrong means one tenant's DB-derived reconcile can write into another tenant's label —
the worst case the F1b security pre-review calls out is "a CNAME inside the tenant's own
label" pointing somewhere it should not, so these are deliberately narrow allow-lists, not
denylists.
"""
import re

DNS_LABEL_RE = re.compile(r"[0-9a-f]{16}")

# ACM DNS validation record names are always a single opaque underscore-prefixed hex/hyphen
# label — e.g. _a79865eb4cd1a6ab990a45779c92cf6f.{label}.{base}. — never something a caller
# picks.
VALIDATION_LEAF_RE = re.compile(r"_[0-9a-f]{8,64}")

VALIDATION_VALUE_SUFFIX = ".acm-validations.aws."

# The observed real ACM DNS-validation CNAME value shape: an opaque 32-hex leaf, a random
# alphanumeric second label, then the fixed acm-validations.aws apex. Not yet confirmed
# against a real ACM certificate (F1b part 1 has no cert bootstrap) — see
# plan/REAL-AWS-VALIDATION.md. Used as a tighter check than the bare suffix in
# assert_valid_validation_value; every real value must also still pass the bare suffix
# check, so a spec inaccuracy here fails safe (rejects a legitimate value) rather than
# open (accepts a hostile one).
VALIDATION_VALUE_RE = re.compile(r"_[0-9a-f]{32}\.[a-z0-9]+\.acm-validations\.aws\.")


class InvalidDnsRecordError(ValueError):
    """A record name or value failed validation and must never reach Route53."""


def normalize_route53_name(name: str) -> str:
    """Route53 returns names with a trailing dot and the wildcard label escaped as
    '\\052'. Canonicalize to no trailing dot and a literal '*' so the ledger, the fake
    zone, and every diff compare like-for-like."""
    denotted = name.removesuffix(".")
    return denotted.replace(r"\052", "*")


def denormalize_for_route53(name: str) -> str:
    """Inverse of normalize_route53_name — what Route53's API itself expects/returns."""
    escaped = name.replace("*", r"\052")
    return escaped if escaped.endswith(".") else f"{escaped}."


def _labels_of(base_domain: str) -> int:
    return len(base_domain.strip(".").split("."))


def validate_dns_label(label: str) -> str:
    # fullmatch, not match: match()+"$" lets a trailing "\n" slip through undetected
    # (Python's $ matches immediately before a trailing newline), which fullmatch does not.
    if not label or not DNS_LABEL_RE.fullmatch(label):
        raise InvalidDnsRecordError(f"{label!r} is not a valid dns_label")
    return label


def edge_record_name(label: str, base_domain: str) -> str:
    return f"edge.{validate_dns_label(label)}.{base_domain}"


def wildcard_record_name(label: str, base_domain: str) -> str:
    return f"*.{validate_dns_label(label)}.{base_domain}"


def assert_owned_record_name(name: str, label: str, base_domain: str, *, kind: str) -> None:
    """Assert `name` (already normalized) is exactly the name this infra's label is allowed
    to own for `kind`. Never accepts a name merely "under" the label — validation names in
    particular are checked as an exact leaf shape, not a wildcard match, because ACM
    controls that leaf and a caller must not be able to substitute one of its own choosing.
    """
    validate_dns_label(label)
    if kind == "edge":
        expected = edge_record_name(label, base_domain)
        if name != expected:
            raise InvalidDnsRecordError(f"edge record name {name!r} != expected {expected!r}")
    elif kind == "wildcard":
        expected = wildcard_record_name(label, base_domain)
        if name != expected:
            raise InvalidDnsRecordError(f"wildcard record name {name!r} != expected {expected!r}")
    elif kind == "validation":
        suffix = f".{label}.{base_domain}"
        if not name.endswith(suffix):
            raise InvalidDnsRecordError(f"validation record name {name!r} not under {suffix!r}")
        leaf = name[: -len(suffix)]
        if not VALIDATION_LEAF_RE.fullmatch(leaf):
            raise InvalidDnsRecordError(f"validation record leaf {leaf!r} is not ACM-shaped")
    else:
        raise InvalidDnsRecordError(f"unknown record kind {kind!r}")


def elb_hostname_pattern(region: str) -> re.Pattern:
    """Bound to the infra's own AWS region — an ELB hostname in another region is either a
    typo or, worst case, a value an attacker with write access to a customer's own EKS
    Ingress status (not IAM-gated the way an ALB is) chose to point somewhere claimable.
    """
    escaped_region = re.escape(region)
    return re.compile(
        rf"(dualstack\.)?[a-z0-9-]+\.{escaped_region}\.elb\.amazonaws\.com\.?"
    )


def assert_valid_edge_target(value: str, region: str) -> None:
    # fullmatch: see validate_dns_label's comment on match()+"$" vs fullmatch.
    if not elb_hostname_pattern(region).fullmatch(value):
        raise InvalidDnsRecordError(
            f"edge target {value!r} is not an ELB hostname in region {region!r}"
        )


def assert_valid_validation_value(value: str) -> None:
    if not VALIDATION_VALUE_RE.fullmatch(value):
        raise InvalidDnsRecordError(
            f"ACM validation value {value!r} does not match the expected shape "
            f"(_<32 hex>.<alnum>{VALIDATION_VALUE_SUFFIX})"
        )


def assert_two_labels_below_apex(name: str, base_domain: str) -> None:
    """Backstop matching the IAM condition itself (route53:ChangeResourceRecordSets is
    permitted only two-or-more labels below the apex) — belt-and-braces so a bug that slips
    past the per-kind checks above still cannot reach the apex or a single-label record.
    """
    if not name.endswith(f".{base_domain}"):
        raise InvalidDnsRecordError(f"{name!r} is not under the platform zone {base_domain!r}")
    remainder = name[: -len(f".{base_domain}")]
    if remainder == "" or "." not in remainder:
        raise InvalidDnsRecordError(f"{name!r} is not at least two labels below the zone apex")
