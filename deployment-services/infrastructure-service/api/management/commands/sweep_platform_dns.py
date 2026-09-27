import logging

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)

# Never delete more than this many records in one sweep run. A bug that makes the ledger
# look emptier than it is should page someone, not silently unwind the whole zone in one
# cron tick.
DEFAULT_MAX_DELETIONS_PER_RUN = 20


class Command(BaseCommand):
    help = (
        "Periodic orphan sweep for the platform DNS zone: diff the zone's actual records "
        "against the PlatformDnsRecord ledger and delete anything present in the zone but "
        "not the ledger. Run only by the dns_writer role — same credential, same process."
    )

    def add_arguments(self, parser):
        parser.add_argument("--max-deletions", type=int, default=DEFAULT_MAX_DELETIONS_PER_RUN)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        from api.common.envs.application import app_config
        from api.models.platform_dns_record import PlatformDnsRecord
        from api.services.platform_dns.route53_client import get_route53_client
        from shared.mode import is_dev_mode

        if not app_config.is_dns_writer:
            raise CommandError(
                "sweep_platform_dns refuses to run: LAUNCHPAD_PROCESS_ROLE must be "
                "'dns_writer'."
            )

        dev_mode = is_dev_mode(app_config.mode)
        client, zone_id = get_route53_client(infra_is_mock=dev_mode, dev_mode=dev_mode)

        zone_names = self._zone_record_names(client, zone_id)
        ledger_keys = {
            (row.record_name, row.record_type)
            for row in PlatformDnsRecord.objects.all()
        }

        # The zone apex NS/SOA and anything single-label are never Launchpad-written (the
        # IAM policy itself cannot reach them — see infra/platform-dns) and must never be
        # treated as candidates for this sweep.
        candidates = {
            key for key in zone_names
            if key not in ledger_keys and self._is_tenant_shaped(key[0])
        }

        if not ledger_keys and candidates:
            raise CommandError(
                "sweep_platform_dns aborting: the ledger is completely empty but the zone "
                f"has {len(candidates)} tenant-shaped record(s). Refusing to treat 'ledger "
                "empty' as 'everything is orphaned' — investigate before running again."
            )

        max_deletions = options["max_deletions"]
        to_delete = list(candidates)[:max_deletions]
        if len(candidates) > max_deletions:
            logger.warning(
                "sweep_platform_dns: %d orphan(s) found, capping this run to %d",
                len(candidates), max_deletions,
            )

        if options["dry_run"]:
            for name, rtype in to_delete:
                self.stdout.write(f"[dry-run] would delete {rtype} {name}")
            return

        for name, rtype in to_delete:
            record = self._find_zone_record(client, zone_id, name, rtype)
            if record is None:
                continue
            client.change_resource_record_sets(
                HostedZoneId=zone_id,
                ChangeBatch={"Changes": [{
                    "Action": "DELETE",
                    "ResourceRecordSet": record,
                }]},
            )
            logger.warning("sweep_platform_dns deleted orphan %s %s", rtype, name)

        self.stdout.write(self.style.SUCCESS(f"sweep_platform_dns deleted {len(to_delete)} orphan(s)"))

    @staticmethod
    def _is_tenant_shaped(name: str) -> bool:
        from api.services.platform_dns import naming
        from django.conf import settings
        try:
            naming.assert_two_labels_below_apex(name, settings.PLATFORM_BASE_DOMAIN)
        except naming.InvalidDnsRecordError:
            return False
        return True

    @staticmethod
    def _zone_record_names(client, zone_id) -> set:
        from api.services.platform_dns import naming
        names = set()
        paginator_kwargs = {"HostedZoneId": zone_id}
        while True:
            response = client.list_resource_record_sets(**paginator_kwargs)
            for record_set in response["ResourceRecordSets"]:
                # CNAME only — the same restriction the IAM write policy itself now
                # enforces (see infra/platform-dns). Every record this writer ever creates
                # is a CNAME; admitting any other type as an orphan candidate would try (and
                # fail with AccessDenied) to delete a record type this credential was never
                # meant to touch, aborting the whole sweep run partway through.
                if record_set["Type"] != "CNAME":
                    continue
                names.add((naming.normalize_route53_name(record_set["Name"]), record_set["Type"]))
            if not response.get("IsTruncated"):
                break
            paginator_kwargs = {
                "HostedZoneId": zone_id,
                "StartRecordName": response["NextRecordName"],
                "StartRecordType": response["NextRecordType"],
            }
        return names

    @staticmethod
    def _find_zone_record(client, zone_id, name, rtype):
        from api.services.platform_dns import naming
        response = client.list_resource_record_sets(HostedZoneId=zone_id)
        for record_set in response["ResourceRecordSets"]:
            if (
                naming.normalize_route53_name(record_set["Name"]) == name
                and record_set["Type"] == rtype
            ):
                return record_set
        return None
