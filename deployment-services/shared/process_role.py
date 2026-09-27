import os

# The only process allowed to hold PLATFORM_DNS_* credentials. Every other process in
# every service (infrastructure-service web/worker, application-service web/worker) must
# refuse to start if those credentials leak into its environment — see
# assert_no_platform_dns_credentials below.
DNS_WRITER_ROLE = "dns_writer"

PROCESS_ROLE_ENV_VAR = "LAUNCHPAD_PROCESS_ROLE"

# Credentials the dns_writer process holds and nothing else may ever hold.
PLATFORM_DNS_CREDENTIAL_ENV_VARS = (
    "PLATFORM_DNS_ACCESS_KEY_ID",
    "PLATFORM_DNS_SECRET_ACCESS_KEY",
)


def current_process_role() -> str:
    return os.environ.get(PROCESS_ROLE_ENV_VAR, "").strip().lower()


def is_dns_writer_role() -> bool:
    return current_process_role() == DNS_WRITER_ROLE


def assert_no_platform_dns_credentials(service_name: str) -> None:
    """Mirror tripwire for every process that is NOT the dns_writer role.

    The platform DNS credential is the only credential in this system that can write to
    the shared, all-tenant Route53 zone across every infrastructure. It must be held by
    exactly one process (LAUNCHPAD_PROCESS_ROLE=dns_writer). Any other process — this
    service's web/worker, or any other service's web/worker — that finds this credential
    in its own environment refuses to start rather than risk using it.
    """
    present = [name for name in PLATFORM_DNS_CREDENTIAL_ENV_VARS if os.environ.get(name)]
    if present:
        raise RuntimeError(
            f"{service_name}: refusing to start — platform DNS credential(s) present in "
            f"this process's environment but LAUNCHPAD_PROCESS_ROLE != {DNS_WRITER_ROLE!r}: "
            f"{', '.join(present)}"
        )
