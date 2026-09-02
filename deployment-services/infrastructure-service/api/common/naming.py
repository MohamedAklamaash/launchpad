import hashlib


def unique_suffix(infra_id) -> str:
    # Name derivation only, never a security control. usedforsecurity=False keeps this
    # working on FIPS-enabled builds, where a plain md5() call raises ValueError. The
    # digest is unchanged, so every already-provisioned resource name stays stable.
    return hashlib.md5(str(infra_id).encode(), usedforsecurity=False).hexdigest()[:8]


def environment_name(infra_id) -> str:
    return f"infra-{str(infra_id)[:8]}-{unique_suffix(infra_id)}"
