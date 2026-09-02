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
