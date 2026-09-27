"""Testable core of run_dns_writer's message handling, separate from the pika/Command
plumbing so it can be exercised directly rather than only through a running consumer.

handle_delivery returns a DeliveryOutcome rather than touching a pika channel directly —
run_dns_writer.py's on_message is a thin adapter that translates the outcome into the
actual ack/nack/republish/DLQ calls. Keeping the decision logic free of pika specifics
means it can be unit tested as a plain function instead of needing to intercept a closure
defined inside Command.handle() (which module-level mock.patch cannot reach: locally
imported names inside a closure are bound once when the enclosing function runs, not
re-resolved from the module's namespace on each call).
"""
import json
import logging
import uuid
from dataclasses import dataclass

from .converge import converge_platform_dns
from .producer import clear_reconcile_dedup

logger = logging.getLogger(__name__)

# botocore error codes that mean "this input will never succeed, no matter how many times
# it's retried" — as opposed to Throttling, ServiceUnavailable, or a network blip, which
# are worth retrying. Route53 raises InvalidChangeBatch for a malformed/conflicting change
# and InvalidInput for a malformed request; naming.py should catch almost everything before
# it reaches Route53, but a real API is the final word on its own validation.
PERMANENT_BOTOCORE_ERROR_CODES = frozenset({"InvalidChangeBatch", "InvalidInput"})


class MalformedReconcileMessage(ValueError):
    """The message body isn't {"infrastructure_id": <uuid>, ...} — never requeue this."""


class PermanentReconcileError(RuntimeError):
    """A Route53 call failed in a way retrying will never fix — discard, don't requeue."""


def parse_reconcile_message(body: bytes) -> tuple[str, int]:
    """Returns (infrastructure_id, attempt). Raises MalformedReconcileMessage for anything
    that isn't a JSON object with a UUID-shaped infrastructure_id — in particular, Django's
    UUIDField raises ValidationError (not caught anywhere else) on a non-UUID string, which
    would otherwise escape as an unclassified exception and be requeued forever."""
    try:
        payload = json.loads(body)
        infra_id = payload["infrastructure_id"]
        attempt = int(payload.get("attempt", 0))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise MalformedReconcileMessage(f"malformed reconcile message: {body!r}") from exc

    try:
        uuid.UUID(str(infra_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise MalformedReconcileMessage(
            f"infrastructure_id is not a UUID: {infra_id!r}"
        ) from exc

    return str(infra_id), attempt


def converge_from_infra_id(infra_id: str, *, dev_mode: bool) -> None:
    """Raises PermanentReconcileError for a Route53 error classified as non-retryable, or
    an api.services.platform_dns.naming/route53_client validation error — both "discard, do
    not requeue" cases; any other exception (a transient Route53/DB/broker error) should be
    retried by the caller.
    """
    # Cleared before converging, not after: a second reconcile trigger for this infra that
    # lands while this message is still being processed must not be coalesced away — it
    # may carry a DB change this convergence hasn't read yet.
    clear_reconcile_dedup(infra_id)

    from api.models.infrastructure import Infrastructure
    infra = Infrastructure.objects.filter(id=infra_id).first()
    # Absence means desired state is empty (see desired_state.py) — still a valid,
    # idempotent convergence, not an error.
    infra_is_mock = bool(infra.is_mock) if infra is not None else dev_mode

    try:
        converge_platform_dns(infra_id, infra_is_mock=infra_is_mock, dev_mode=dev_mode)
    except Exception as exc:
        from botocore.exceptions import ClientError
        if isinstance(exc, ClientError):
            code = exc.response.get("Error", {}).get("Code", "")
            if code in PERMANENT_BOTOCORE_ERROR_CODES:
                raise PermanentReconcileError(f"permanent Route53 error {code}: {exc}") from exc
        raise

    # F1b part 3a: a successful converge may have just changed dns_synced (the ledger's
    # synced_at moved, or the edge/wildcard pair was created or torn down) — refresh
    # application-service's read-model snapshot. Best-effort, never raises.
    from api.services.host_readiness import publish_host_readiness
    publish_host_readiness(infra_id)


def process_reconcile_message(body: bytes, *, dev_mode: bool) -> None:
    """Convenience wrapper used by tests that only care about success/exception, not the
    ack/retry/DLQ decision — see handle_delivery for that."""
    infra_id, _attempt = parse_reconcile_message(body)
    converge_from_infra_id(infra_id, dev_mode=dev_mode)


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """What run_dns_writer.py's on_message should do with one RabbitMQ delivery.

    action is one of:
      "ack"     — converged successfully; ack the delivery.
      "discard" — malformed message or a permanent/validation error; nack, requeue=False.
      "retry"   — a transient failure with attempts remaining; ack this delivery (it's
                  done) and publish a new message with next_attempt.
      "dlq"     — a transient failure with attempts exhausted; ack this delivery and
                  publish to the DLQ instead of retrying again.
    """
    action: str
    infra_id: str | None = None
    next_attempt: int | None = None
    reason: str | None = None


def handle_delivery(body: bytes, *, dev_mode: bool, max_attempts: int) -> DeliveryOutcome:
    from .naming import InvalidDnsRecordError
    from .route53_client import MockRealMismatch, PlatformDnsMisconfigured

    try:
        infra_id, attempt = parse_reconcile_message(body)
    except MalformedReconcileMessage:
        logger.error("platform DNS reconcile message malformed — discarding: %r", body)
        return DeliveryOutcome(action="discard")

    try:
        converge_from_infra_id(infra_id, dev_mode=dev_mode)
        return DeliveryOutcome(action="ack")
    except (InvalidDnsRecordError, MockRealMismatch, PlatformDnsMisconfigured, PermanentReconcileError):
        # A data or spec problem, not a transient one — e.g. a hostile/wrong-region alb_dns
        # on an EKS infra, whose Ingress status is customer-writable, or a Route53
        # InvalidChangeBatch/InvalidInput no retry will fix. With prefetch_count=1 and a
        # single consumer, requeuing forever would starve every other infra's reconcile.
        logger.exception(
            "platform DNS reconcile for %s rejected — discarding: %r", infra_id, body,
        )
        return DeliveryOutcome(action="discard")
    except Exception:
        logger.exception(
            "platform DNS reconcile for %s failed (attempt %d)", infra_id, attempt,
        )
        if attempt + 1 >= max_attempts:
            return DeliveryOutcome(
                action="dlq", infra_id=infra_id, next_attempt=attempt,
                reason="max reconcile attempts exceeded",
            )
        return DeliveryOutcome(action="retry", infra_id=infra_id, next_attempt=attempt + 1)
