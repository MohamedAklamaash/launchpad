import os

DEV_MODE = "dev"
PROD_MODE = "prod"

REAL_AWS_CREDENTIAL_ENV_VARS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_ROLE_ARN",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
)

_PLACEHOLDER_CREDENTIAL_VALUES = frozenset(
    {
        "your_aws_access_key",
        "your_aws_secret_key",
        "changeme",
        "placeholder",
        "dev",
        "mock",
        "none",
        "test",
    }
)


def normalize_mode(raw) -> str:
    value = (raw or "").strip().lower()
    return DEV_MODE if value == DEV_MODE else PROD_MODE


def is_dev_mode(mode) -> bool:
    return normalize_mode(mode) == DEV_MODE


def _is_real_credential_value(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    return candidate.lower() not in _PLACEHOLDER_CREDENTIAL_VALUES


def detected_real_aws_credentials() -> list[str]:
    return [
        name
        for name in REAL_AWS_CREDENTIAL_ENV_VARS
        if _is_real_credential_value(os.environ.get(name, ""))
    ]


def real_aws_credentials_present() -> bool:
    return bool(detected_real_aws_credentials())


def enforce_dev_mode_safety(mode, service_name: str, logger) -> None:
    if not is_dev_mode(mode):
        logger.warning("%s booting in MODE=prod — real AWS path active.", service_name)
        return

    detected = detected_real_aws_credentials()
    if detected:
        logger.warning(
            "%s booting in MODE=dev — AWS calls are MOCKED. Real-looking AWS env vars are "
            "present and will be IGNORED: %s",
            service_name,
            ", ".join(detected),
        )
    else:
        logger.warning("%s booting in MODE=dev — AWS calls are MOCKED.", service_name)

