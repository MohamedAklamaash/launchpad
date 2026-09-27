"""Testable core of run_dns_writer's message handling, separate from the pika/Command
plumbing so it can be exercised directly rather than only through a running consumer.
"""
import json
import logging

from .converge import converge_platform_dns
from .producer import clear_reconcile_dedup

logger = logging.getLogger(__name__)


class MalformedReconcileMessage(ValueError):
    """The message body isn't {"infrastructure_id": ...} — never requeue this."""


def process_reconcile_message(body: bytes, *, dev_mode: bool) -> None:
    """Raises MalformedReconcileMessage or an api.services.platform_dns.naming/
    route53_client validation error for "discard, do not requeue" cases; any other
    exception (a transient Route53/DB/broker error) should be requeued by the caller.
    """
    try:
        payload = json.loads(body)
        infra_id = payload["infrastructure_id"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise MalformedReconcileMessage(f"malformed reconcile message: {body!r}") from exc

    # Cleared before converging, not after: a second reconcile trigger for this infra that
    # lands while this message is still being processed must not be coalesced away — it
    # may carry a DB change this convergence hasn't read yet.
    clear_reconcile_dedup(infra_id)

    from api.models.infrastructure import Infrastructure
    infra = Infrastructure.objects.filter(id=infra_id).first()
    # Absence means desired state is empty (see desired_state.py) — still a valid,
    # idempotent convergence, not an error.
    infra_is_mock = bool(infra.is_mock) if infra is not None else dev_mode
    converge_platform_dns(infra_id, infra_is_mock=infra_is_mock, dev_mode=dev_mode)
