"""Calls infrastructure-service's internal exit-status lookup (H2 RECOMMENDED 2 — see
plan/H-hardening.md and api/management/commands/clear_infrastructure_exited.py).

Same client shape as custom_domain_routing.py's
notify_infrastructure_service_of_deleted_application — except here a failure to reach
infrastructure-service must NOT be swallowed as best-effort: the caller (the clear
command) needs to refuse, not silently proceed, when it cannot confirm the real status.
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class ExitStatusLookupError(Exception):
    """Raised whenever the remote status could not be confirmed — unreachable service,
    non-200 response, or a malformed body. The caller must treat this as "refuse", never
    as "assume not exited"."""


def fetch_remote_exit_status(infrastructure_id) -> str | None:
    """Returns infrastructure-service's own `exited_at` (an ISO string, or None if it is
    not exited there) for the given infrastructure id. Raises ExitStatusLookupError on
    any failure to get a trustworthy answer — a 404 (infrastructure-service has no such
    row) counts as a failure here too, since "I couldn't find it" is not the same claim as
    "it is confirmed not exited"."""
    from shared.resilience.http_client import ResilientHttpClient

    client = ResilientHttpClient(
        name="InfrastructureServiceExitStatusClient",
        base_url=settings.INFRASTRUCTURE_SERVICE_URL, timeout=5,
    )
    try:
        response = client.get(
            "/api/v1/internal/infrastructures/exit-status/",
            params={"infrastructure_id": str(infrastructure_id)},
            headers={"X-INTERNAL-TOKEN": settings.INTERNAL_AUTH_TOKEN},
            timeout=(2, 5),
        )
    except Exception as exc:
        raise ExitStatusLookupError(f"infrastructure-service unreachable: {exc}") from exc

    if response.status_code != 200:
        raise ExitStatusLookupError(f"infrastructure-service returned HTTP {response.status_code}")

    try:
        return response.json().get("exited_at")
    except Exception as exc:
        raise ExitStatusLookupError(f"malformed response body: {exc}") from exc
