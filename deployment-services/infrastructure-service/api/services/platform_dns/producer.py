"""Publish a reconcile intent for the platform DNS writer.

The message is `{"infrastructure_id": ...}` — nothing else. The writer derives every name
and value it will touch from the DB itself (desired_state.py); the message only says which
infrastructure to look at. A forged or replayed message therefore cannot make the writer do
anything beyond re-converging that one infrastructure's own current DB-derived state.
"""
import logging
import os

from api.common.envs.application import app_config
from django.conf import settings
from shared.resilience import ResilientPikaProducer

logger = logging.getLogger(__name__)

DNS_RECONCILE_EXCHANGE = "platform_dns.events"
DNS_RECONCILE_ROUTING_KEY = "platform_dns.reconcile"
DNS_RECONCILE_QUEUE = "infrastructure-service.platform-dns-writer"

# A transient failure (Route53 throttling, a DB blip) is retried with a short backoff up to
# this many times, tracked via the "attempt" field on the message itself — not RabbitMQ's
# native redelivery, which carries no counter. Beyond this, the message goes to the DLQ
# instead of looping forever: with a single consumer at prefetch_count=1, an endlessly
# retried message for one infra would starve every other infra's reconcile behind it.
MAX_RECONCILE_ATTEMPTS = int(os.environ.get("PLATFORM_DNS_MAX_RECONCILE_ATTEMPTS", "5"))
DNS_RECONCILE_DLQ_QUEUE = "infrastructure-service.platform-dns-writer.dlq"
DNS_RECONCILE_DLQ_ROUTING_KEY = "platform_dns.reconcile.dlq"

# Coalescing window: Route53 is a shared ~5 req/s/account budget, and several DB changes
# for one infra (e.g. a database create finishing alongside an environment update) can each
# trigger a reconcile call within moments of each other. Convergence is idempotent and
# reads current DB state regardless of which trigger fired it, so collapsing bursts within
# this window only saves API calls — it never changes what gets converged.
_COALESCE_WINDOW_SECONDS = 20


def _dedup_key(infra_id: str) -> str:
    return f"platform_dns:reconcile_pending:{infra_id}"


_producer: ResilientPikaProducer | None = None


def _get_producer() -> ResilientPikaProducer:
    global _producer
    if _producer is None:
        _producer = ResilientPikaProducer(
            url=app_config.rabbitmq_url,
            exchange=DNS_RECONCILE_EXCHANGE,
            name="platform-dns-reconcile-producer",
        )
    return _producer


def clear_reconcile_dedup(infrastructure_id) -> None:
    """Clear the coalescing key. Called by the writer at the start of processing a message
    (before converge_platform_dns), not after — otherwise a second _save_outputs-triggered
    reconcile that lands while the first is still being processed would be coalesced away
    and its change lost until some unrelated trigger happens to fire later."""
    try:
        import redis
        client = redis.Redis(
            host=settings.REDIS_HOST, port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD, db=settings.REDIS_DB,
            decode_responses=True, socket_timeout=2, socket_connect_timeout=2,
        )
        client.delete(_dedup_key(str(infrastructure_id)))
    except Exception:
        logger.warning(
            "platform DNS reconcile dedup clear failed for %s", infrastructure_id, exc_info=True,
        )


def request_dns_reconcile(infrastructure_id, *, coalesce: bool = True) -> None:
    """Best-effort publish — see ResilientPikaProducer.publish(), which buffers rather than
    raising on a broker outage. Callers on a critical path (destroy) must not rely on this
    raising to detect writer unavailability; they poll PlatformDnsRecord for confirmation
    instead (see teardown.py) and pass coalesce=False so a debounce window from an
    unrelated, still-pending reconcile can never suppress theirs.
    """
    infra_id = str(infrastructure_id)

    if coalesce:
        try:
            import redis
            client = redis.Redis(
                host=settings.REDIS_HOST, port=settings.REDIS_PORT,
                password=settings.REDIS_PASSWORD, db=settings.REDIS_DB,
                decode_responses=True, socket_timeout=2, socket_connect_timeout=2,
            )
            if not client.set(_dedup_key(infra_id), "1", nx=True, ex=_COALESCE_WINDOW_SECONDS):
                logger.debug("platform DNS reconcile for %s coalesced (already pending)", infra_id)
                return
        except Exception:
            logger.warning(
                "platform DNS reconcile dedup check failed for %s; publishing anyway",
                infra_id, exc_info=True,
            )

    producer = _get_producer()
    try:
        producer.connect()
    except Exception:
        logger.warning("platform DNS reconcile producer connect failed for %s", infra_id, exc_info=True)
    producer.publish(DNS_RECONCILE_ROUTING_KEY, {"infrastructure_id": infra_id, "attempt": 0})
    logger.info("platform DNS reconcile requested for %s", infra_id)


def republish_reconcile_with_attempt(infrastructure_id, attempt: int) -> None:
    """Used only by run_dns_writer's own retry loop after a transient failure — never
    coalesced (a retry must not be silently dropped by an unrelated debounce window) and
    carries the incremented attempt count RabbitMQ's native redelivery has no field for."""
    infra_id = str(infrastructure_id)
    producer = _get_producer()
    try:
        producer.connect()
    except Exception:
        logger.warning("platform DNS reconcile retry publish connect failed for %s", infra_id, exc_info=True)
    producer.publish(DNS_RECONCILE_ROUTING_KEY, {"infrastructure_id": infra_id, "attempt": attempt})
    logger.warning("platform DNS reconcile for %s republished (attempt %d)", infra_id, attempt)


def declare_dlq(rabbitmq_url: str) -> None:
    """Declare and bind the DLQ queue once at writer startup. A topic exchange only routes
    to queues that are already bound at publish time, so publish_to_dlq below would
    silently drop messages if nothing had declared this queue first."""
    import pika

    parameters = pika.URLParameters(rabbitmq_url)
    connection = pika.BlockingConnection(parameters)
    try:
        channel = connection.channel()
        channel.exchange_declare(exchange=DNS_RECONCILE_EXCHANGE, exchange_type="topic", durable=True)
        channel.queue_declare(queue=DNS_RECONCILE_DLQ_QUEUE, durable=True)
        channel.queue_bind(
            queue=DNS_RECONCILE_DLQ_QUEUE, exchange=DNS_RECONCILE_EXCHANGE,
            routing_key=DNS_RECONCILE_DLQ_ROUTING_KEY,
        )
    finally:
        connection.close()


def publish_to_dlq(infrastructure_id, attempt: int, reason: str) -> None:
    infra_id = str(infrastructure_id)
    producer = _get_producer()
    try:
        producer.connect()
    except Exception:
        logger.warning("platform DNS DLQ publish connect failed for %s", infra_id, exc_info=True)
    producer.publish(DNS_RECONCILE_DLQ_ROUTING_KEY, {
        "infrastructure_id": infra_id, "attempt": attempt, "reason": reason,
    })
    logger.error(
        "platform DNS reconcile for %s moved to DLQ after %d attempt(s): %s",
        infra_id, attempt, reason,
    )
