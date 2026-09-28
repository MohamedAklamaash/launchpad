"""H2 (hardening) — refuse mutating/deploying actions once an infrastructure has exited.

infrastructure-service owns `Infrastructure.exited_at` and refuses its own mutating
endpoints (reprovision, config update) directly against that field (F6 Decision 14).
application-service only has a read-model mirror of it, kept current by
`InfraExitedEventConsumer` (api/messaging/consumers/infrastructure.py) — this module is
the single place that turns "the mirror says exited" into a refusal, so every call site
raises and is mapped the same way rather than re-implementing the check.
"""


class InfrastructureExitedError(Exception):
    """Raised when a mutating or deploying action targets an infrastructure whose
    read-model mirror shows `exited_at` set. Every call site maps this to HTTP 409 with
    `code: "infrastructure_exited"` — see api/views/application.py's error mapping."""

    code = "infrastructure_exited"

    def __init__(self, message: str = "This infrastructure has exited and no longer accepts deploys or changes."):
        super().__init__(message)


def require_not_exited(infra) -> None:
    """Raise InfrastructureExitedError if this infrastructure's mirrored exited_at is set.
    `infra` is application-service's own Infrastructure row (or None, e.g. a stale read-
    model — treated as not-exited here since ValueError("Infrastructure not found") is the
    existing, more specific failure for that case at every call site)."""
    if infra is not None and getattr(infra, "exited_at", None) is not None:
        raise InfrastructureExitedError()
