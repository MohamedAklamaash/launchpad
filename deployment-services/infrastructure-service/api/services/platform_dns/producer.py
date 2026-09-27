"""Publish a reconcile intent for the platform DNS writer.

The message is `{"infrastructure_id": ...}` — nothing else. The writer derives every name
and value it will touch from the DB itself (desired_state.py); the message only says which
infrastructure to look at. A forged or replayed message therefore cannot make the writer do
anything beyond re-converging that one infrastructure's own current DB-derived state.
"""
import logging

from api.common.envs.application import app_config
from django.conf import settings
from shared.resilience import ResilientPikaProducer

logger = logging.getLogger(__name__)

DNS_RECONCILE_EXCHANGE = "platform_dns.events"
DNS_RECONCILE_ROUTING_KEY = "platform_dns.reconcile"
DNS_RECONCILE_QUEUE = "infrastructure-service.platform-dns-writer"

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
    producer.publish(DNS_RECONCILE_ROUTING_KEY, {"infrastructure_id": infra_id})
    logger.info("platform DNS reconcile requested for %s", infra_id)
