import logging

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Periodic catch-up for platform DNS ledger rows still awaiting Route53 GetChange "
        "confirmation (F1b part 3a; RECOMMENDED item 1, security review). "
        "converge_platform_dns itself never sleeps waiting for a change to reach INSYNC — "
        "doing so on the writer's single prefetch_count=1 consumer thread would delay "
        "every other queued reconcile, including a teardown. This command is the "
        "out-of-process side of that trade-off: run it on a schedule (cron/systemd timer), "
        "same operational pattern as sweep_platform_dns. Run only by the dns_writer role — "
        "same credential, same process."
    )

    def handle(self, *args, **options):
        from api.common.envs.application import app_config
        from api.services.platform_dns.converge import sweep_pending_dns_syncs
        from api.services.platform_dns.route53_client import get_route53_client
        from shared.mode import is_dev_mode

        if not app_config.is_dns_writer:
            raise CommandError(
                "sync_platform_dns_changes refuses to run: LAUNCHPAD_PROCESS_ROLE must be "
                "'dns_writer'."
            )

        dev_mode = is_dev_mode(app_config.mode)
        # Mirrors sweep_platform_dns.py's own gate: this environment is either entirely
        # mock/dev or entirely real, never a mix of infrastructures needing different
        # Route53 clients within the same run.
        client, _zone_id = get_route53_client(infra_is_mock=dev_mode, dev_mode=dev_mode)

        confirmed = sweep_pending_dns_syncs(client)
        self.stdout.write(self.style.SUCCESS(
            f"sync_platform_dns_changes: confirmed {confirmed} record(s) INSYNC"
        ))
