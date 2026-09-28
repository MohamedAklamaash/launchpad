"""H2 (hardening) R2(b) — republish `infrastructure.exited` for every already-exited
infrastructure. Idempotent: application-service's `InfraExitedEventConsumer` applies a
first-wins latch (see plan/H-hardening.md), so republishing an infra whose exit already
landed is a no-op there.

This is the backstop, not the primary delivery path. The primary path is the direct
publish from `complete_exit` (plus its own 409 self-heal); this function exists for the
window before either side has ever run against the other — see
`api/management/commands/republish_exited_events.py` (an on-demand operator run) and
`api/management/commands/run_worker.py` (a periodic tick calling this automatically, so an
operator does not have to remember to run the command at all).
"""
import logging

logger = logging.getLogger(__name__)


def republish_all_exited_infrastructures() -> int:
    """Connects explicitly before publishing (see `producer.py:InfraEventProducer.connect`)
    so an unreachable broker raises here rather than silently vanishing into
    `ResilientPikaProducer`'s internal buffer — the caller decides whether that should be
    fatal (the management command) or logged-and-retried-next-tick (the worker).
    Returns the number of infrastructures (re)published."""
    from api.messaging.producer.producer import infra_producer
    from api.models.infrastructure import Infrastructure

    infra_producer.connect()
    count = 0
    for infra in Infrastructure.objects.filter(exited_at__isnull=False).iterator():
        infra_producer.publish_infrastructure_exited(infra.id, infra.exited_at)
        count += 1
    return count
