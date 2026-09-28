import hashlib


def unique_suffix(infra_id) -> str:
    # Name derivation only, never a security control. usedforsecurity=False keeps this
    # working on FIPS-enabled builds, where a plain md5() call raises ValueError. The
    # digest is unchanged, so every already-provisioned resource name stays stable.
    return hashlib.md5(str(infra_id).encode(), usedforsecurity=False).hexdigest()[:8]


def environment_name(infra_id) -> str:
    # H4 review: the leading `str(infra_id)[:8]` here is a UUIDv7 prefix and would be a
    # uniqueness key on its own, but it isn't one — `unique_suffix` (a full-id hash,
    # never a slice) is what actually disambiguates two environment names, so two
    # infras created in the same ~65s window still get distinct names even though
    # their prefixes match. Left as-is: it's purely a human-readable label prefix, and
    # renaming it would move a live Terraform-managed resource name.
    return f"infra-{str(infra_id)[:8]}-{unique_suffix(infra_id)}"
