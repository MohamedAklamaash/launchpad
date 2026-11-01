OFF_VALUES = {"false", "0", "no", "off"}


def flag_default_on(value) -> bool:
    """On unless explicitly off. Unset and empty are on; matches auth-service and the frontend."""
    if value is None:
        return True
    return str(value).strip().lower() not in OFF_VALUES
