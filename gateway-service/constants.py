import re

EXEMPT_PATHS = [
    "/health",
    "/liveness",
    "/readiness",
    "/docs",
    "/openapi.json",
]

# GET-only exemptions, for endpoints a dashboard legitimately polls (database status).
# Method-scoped on purpose: a prefix-based path exemption would also unthrottle POST
# /api/infrastructures/<id>/databases/, which runs a synchronous AssumeRole +
# iam:SimulatePrincipalPolicy — a cost-amplification vector if left unthrottled.
EXEMPT_GET_PATH_PATTERNS = [
    re.compile(r"^/api/infrastructures/[^/]+/databases/?$"),
    re.compile(r"^/api/infrastructures/[^/]+/databases/[^/]+/?$"),
]


def is_rate_limit_exempt(method: str, path: str) -> bool:
    if path in EXEMPT_PATHS:
        return True
    if method == "GET":
        return any(pattern.match(path) for pattern in EXEMPT_GET_PATH_PATTERNS)
    return False