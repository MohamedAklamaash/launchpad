import logging

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Run the platform DNS writer: the only process allowed to hold PLATFORM_DNS_* "
        "credentials. Consumes reconcile intents off a dedicated RabbitMQ queue and "
        "converges the platform Route53 zone to each infrastructure's DB-derived state."
    )

    def handle(self, *args, **options):
        from api.common.envs.application import app_config
        from api.services.platform_dns.dispatch import (
            MalformedReconcileMessage,
            process_reconcile_message,
        )
        from api.services.platform_dns.naming import InvalidDnsRecordError
        from api.services.platform_dns.producer import (
            DNS_RECONCILE_EXCHANGE,
            DNS_RECONCILE_QUEUE,
            DNS_RECONCILE_ROUTING_KEY,
        )
        from api.services.platform_dns.route53_client import (
            MockRealMismatch,
            PlatformDnsMisconfigured,
            assert_caller_identity,
            load_platform_dns_config,
        )
        from shared.mode import enforce_dev_mode_safety, is_dev_mode
        from shared.resilience import ResilientPikaConsumer

        if not app_config.is_dns_writer:
            raise CommandError(
                "run_dns_writer refuses to start: LAUNCHPAD_PROCESS_ROLE must be "
                "'dns_writer' (see api/common/envs/application.py)."
            )

        enforce_dev_mode_safety(app_config.mode, "infrastructure-service dns_writer", logger)
        dev_mode = is_dev_mode(app_config.mode)

        if not dev_mode:
            config = load_platform_dns_config()
            if config is None:
                raise CommandError(
                    "run_dns_writer refuses to start in production: PLATFORM_DNS_ZONE_ID/"
                    "ACCOUNT_ID/ACCESS_KEY_ID/SECRET_ACCESS_KEY must all be set."
                )
            try:
                assert_caller_identity(config)
            except PlatformDnsMisconfigured as exc:
                raise CommandError(str(exc)) from exc

        def on_message(channel, method, properties, body):
            from django.db import connection
            # Mirrors application_consumer.py: a long-idle consumer's connection can go
            # stale between messages; close it so Django reconnects rather than failing the
            # first query of this callback.
            connection.close()

            try:
                process_reconcile_message(body, dev_mode=dev_mode)
                channel.basic_ack(delivery_tag=method.delivery_tag)
            except MalformedReconcileMessage:
                logger.error("platform DNS reconcile message malformed — discarding: %r", body)
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            except (InvalidDnsRecordError, MockRealMismatch, PlatformDnsMisconfigured):
                # A data problem, not a transient one — e.g. a hostile/wrong-region alb_dns
                # on an EKS infra, whose Ingress status is customer-writable. Requeuing
                # would redeliver forever and, with prefetch_count=1, starve every other
                # infra's reconcile behind it.
                logger.exception(
                    "platform DNS reconcile rejected on validation — discarding: %r", body,
                )
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            except Exception:
                logger.exception("platform DNS reconcile failed — requeueing: %r", body)
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

        consumer = ResilientPikaConsumer(
            url=app_config.rabbitmq_url,
            exchange=DNS_RECONCILE_EXCHANGE,
            queue=DNS_RECONCILE_QUEUE,
            routing_key=DNS_RECONCILE_ROUTING_KEY,
            name="platform-dns-writer",
            prefetch_count=1,
        )

        self.stdout.write(self.style.SUCCESS(
            f"platform DNS writer starting (dev_mode={dev_mode}) — consuming "
            f"{DNS_RECONCILE_QUEUE}"
        ))
        consumer.start(on_message)
