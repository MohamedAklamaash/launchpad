EXEMPT_PATHS = [
    "/health",
    "/liveness",
    "/readiness",
    "/docs",
    "/openapi.json",
]


def is_rate_limit_exempt(method: str, path: str) -> bool:
    return path in EXEMPT_PATHS
