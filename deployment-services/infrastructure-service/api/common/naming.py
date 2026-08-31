import hashlib


def unique_suffix(infra_id) -> str:
    return hashlib.md5(str(infra_id).encode()).hexdigest()[:8]


def environment_name(infra_id) -> str:
    return f"infra-{str(infra_id)[:8]}-{unique_suffix(infra_id)}"
