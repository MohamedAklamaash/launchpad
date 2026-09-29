import ipaddress
import re

DATABASE_NAME_RE = re.compile(r'^[a-z][a-z0-9-]{2,30}$')


def validate_database_name(name: str) -> None:
    """Reject anything not shaped like a safe database identifier.

    `name` is interpolated into generated Terraform HCL, an AWS secret name, and a
    snapshot identifier — validate once here at every sink that reaches it, including
    the terraform worker's interpolation point, not just the API create boundary.
    """
    if not isinstance(name, str) or not DATABASE_NAME_RE.fullmatch(name):
        raise ValueError("Invalid database name")


MIN_EKS_PUBLIC_ACCESS_PREFIXLEN = 16

EKS_PUBLIC_ACCESS_CIDRS_GUIDANCE = (
    "EKS_PUBLIC_ACCESS_CIDRS is misconfigured: set it to a comma-separated list of CIDRs, "
    f"each narrower than /{MIN_EKS_PUBLIC_ACCESS_PREFIXLEN}, that may reach the cluster's "
    "public API endpoint (never 0.0.0.0/0 or an equivalent split, e.g. 0.0.0.0/1 + 128.0.0.0/1)."
)


def validate_eks_public_access_cidrs(cidrs) -> None:
    """Reject an EKS public-access CIDR list that is empty, malformed, or wide enough to
    leave the cluster's API endpoint open to the whole internet.

    Stdlib-only (no Django import) so it can run both from the provisioning worker's
    Terraform config generation (api/services/terraform_worker.py) and from
    core/settings.py's startup check, which executes before the Django app registry is
    ready and cannot import anything that touches models.

    Raises a single fixed, non-secret message with no CIDR value interpolated in: the
    provisioning worker's error_message goes through allowlist log redaction
    (api/services/log_redaction.py), which drops any line it doesn't recognize, so a
    message built by interpolating the offending CIDR would read as
    "… (N lines withheld)" instead of the guidance below.
    """
    if not cidrs:
        raise ValueError(EKS_PUBLIC_ACCESS_CIDRS_GUIDANCE)
    for cidr in cidrs:
        try:
            network = ipaddress.ip_network(str(cidr), strict=True)
        except ValueError as exc:
            raise ValueError(EKS_PUBLIC_ACCESS_CIDRS_GUIDANCE) from exc
        if network.prefixlen < MIN_EKS_PUBLIC_ACCESS_PREFIXLEN:
            raise ValueError(EKS_PUBLIC_ACCESS_CIDRS_GUIDANCE)
