"""Shared hostname syntax validation for customer custom domains (F1b part 3b).

Used by infrastructure-service (right after IDNA normalization, at claim time — see
`api/models/custom_domain.py`) and by application-service (a defense-in-depth re-check on
the value it receives over the internal attach endpoint, since a hostname reaching an ALB
host-header condition is a config-injection sink and application-service must not simply
trust a value handed to it by another service).
"""
import ipaddress
import re

_LDH_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
MAX_HOSTNAME_LENGTH = 253
MAX_LABEL_LENGTH = 63


class InvalidHostnameError(ValueError):
    """Hostname fails basic DNS syntax before it may reach ALB conditions, ACM, or a
    resolver."""


def validate_hostname_syntax(hostname: str) -> None:
    """Enforce plain DNS hostname syntax on an already-normalized (ASCII/punycode,
    lowercased) value, before it can reach an ALB host-header condition, ACM's
    `DomainName`, or a DNS query. Apex/root domains are allowed through here (>=2 labels
    only) — rejecting apex is a documented product decision (see F1b's "Out of scope"),
    not a syntax rule, and is enforced separately if ever needed.
    """
    if len(hostname) > MAX_HOSTNAME_LENGTH:
        raise InvalidHostnameError(f"{hostname!r} exceeds {MAX_HOSTNAME_LENGTH} characters.")
    if "*" in hostname:
        raise InvalidHostnameError(f"{hostname!r} contains a wildcard, which is not a claimable hostname.")

    labels = hostname.split(".")
    if len(labels) < 2:
        raise InvalidHostnameError(f"{hostname!r} must have at least two labels.")
    for label in labels:
        if len(label) > MAX_LABEL_LENGTH:
            raise InvalidHostnameError(f"label {label!r} in {hostname!r} exceeds {MAX_LABEL_LENGTH} characters.")
        if not _LDH_LABEL_RE.match(label):
            raise InvalidHostnameError(f"label {label!r} in {hostname!r} is not a valid LDH label.")

    # An all-numeric TLD is never a real public suffix — some HTTP clients and OS resolvers
    # interpret a hostname whose last label is entirely digits as legacy dotted-decimal/hex
    # IP shorthand (e.g. `0x7f.1` as 127.0.0.1), which would let a claimed "hostname" quietly
    # resolve as an IP literal to a client that treats it that way, bypassing the IP-literal
    # check below (which only catches a value `ipaddress.ip_address` accepts outright).
    if labels[-1].isdigit():
        raise InvalidHostnameError(f"{hostname!r} has an all-numeric top-level label, not a valid TLD.")

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise InvalidHostnameError(f"{hostname!r} is an IP address literal, not a hostname.")
