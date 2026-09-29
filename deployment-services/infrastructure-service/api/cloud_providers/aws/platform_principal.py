import re

from django.conf import settings

# arn:aws:iam::<12-digit account>:user/<name>, the same shape settings.py requires of
# LAUNCHPAD_PLATFORM_PRINCIPAL_ARN (see core/settings.py's validation at import time).
_PRINCIPAL_ARN_RE = re.compile(r"^arn:aws:iam::(\d{12}):user/([^/]+)$")


def platform_account_and_user() -> tuple[str, str]:
    """Split settings.LAUNCHPAD_PLATFORM_PRINCIPAL_ARN into (account_id, user_name).

    These are the values the customer's create_aws_role.sh needs to build a trust
    policy naming the right principal. See LAUNCHPAD_PLATFORM_ACCOUNT_ID /
    LAUNCHPAD_PLATFORM_USER in that script.
    """
    arn = settings.LAUNCHPAD_PLATFORM_PRINCIPAL_ARN
    match = _PRINCIPAL_ARN_RE.match(arn)
    if not match:
        raise ValueError(
            f"LAUNCHPAD_PLATFORM_PRINCIPAL_ARN is not a user ARN of the form "
            f"arn:aws:iam::<account>:user/<name>: {arn!r}"
        )
    return match.group(1), match.group(2)
