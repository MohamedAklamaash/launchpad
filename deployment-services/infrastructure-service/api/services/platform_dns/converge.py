"""Diff desired state against the ledger and issue the minimal set of Route53 writes.

Every name this module hands to Route53 has already been validated in desired_state.py /
naming.py — this module re-validates once more immediately before the call as pure defense
in depth. All changes for one infrastructure are sent as a single Route53 ChangeBatch:
Route53 applies a batch atomically, so there is no window where only some of a reconcile's
writes have landed — in particular no window where a stale wildcard record outlives the
edge record it points at. Ledger rows for the upserted names are written BEFORE the call;
ledger rows for the deleted names are removed only AFTER the call confirms success — see
PlatformDnsRecord's docstring for why that ordering is what makes "ledger is empty" a safe
proxy for "nothing of ours is left in the zone", including across a hard delete of the
Infrastructure row.
"""
import logging

from api.models.platform_dns_record import PlatformDnsRecord
from django.conf import settings
from django.db import transaction

from . import naming
from .desired_state import DesiredRecord, compute_desired_state
from .route53_client import get_route53_client

logger = logging.getLogger(__name__)

# Deletes are ordered wildcard -> edge -> validation within a batch purely for audit-log
# readability; Route53 applies one ChangeBatch atomically, so this ordering carries no
# correctness weight on its own — the atomicity is what removes the dangling-window risk a
# sequential per-record delete would have.
_KIND_DELETE_ORDER = {"wildcard": 0, "edge": 1, "validation": 2}


def converge_platform_dns(infrastructure_id: str, *, infra_is_mock: bool, dev_mode: bool) -> None:
    base_domain = settings.PLATFORM_BASE_DOMAIN
    desired = compute_desired_state(infrastructure_id)
    for record in desired:
        naming.assert_two_labels_below_apex(record.name, base_domain)
    desired_by_key = {(record.name, record.record_type): record for record in desired}

    ledger_by_key = {
        (row.record_name, row.record_type): row
        for row in PlatformDnsRecord.objects.filter(infrastructure_id=infrastructure_id)
    }

    to_upsert = [
        record for key, record in desired_by_key.items()
        if key not in ledger_by_key
        or ledger_by_key[key].record_value != record.value
        or ledger_by_key[key].ttl != record.ttl
    ]
    to_delete = sorted(
        (row for key, row in ledger_by_key.items() if key not in desired_by_key),
        key=lambda row: _KIND_DELETE_ORDER.get(row.kind, 99),
    )

    if not to_upsert and not to_delete:
        return

    client, zone_id = get_route53_client(infra_is_mock=infra_is_mock, dev_mode=dev_mode)

    with transaction.atomic():
        for record in to_upsert:
            PlatformDnsRecord.objects.update_or_create(
                infrastructure_id=infrastructure_id,
                record_name=record.name,
                record_type=record.record_type,
                defaults={"kind": record.kind, "record_value": record.value, "ttl": record.ttl},
            )

    changes = [_upsert_change(record) for record in to_upsert] + [_delete_change(row) for row in to_delete]
    client.change_resource_record_sets(HostedZoneId=zone_id, ChangeBatch={"Changes": changes})

    if to_delete:
        PlatformDnsRecord.objects.filter(
            id__in=[row.id for row in to_delete],
        ).delete()

    for record in to_upsert:
        logger.info(
            "platform DNS upserted %s record for infra %s: %s -> %s",
            record.kind, infrastructure_id, record.name, record.value,
        )
    for row in to_delete:
        logger.info(
            "platform DNS deleted %s record for infra %s: %s",
            row.kind, infrastructure_id, row.record_name,
        )


def _upsert_change(record: DesiredRecord) -> dict:
    return {
        "Action": "UPSERT",
        "ResourceRecordSet": {
            "Name": naming.denormalize_for_route53(record.name),
            "Type": record.record_type,
            "TTL": record.ttl,
            "ResourceRecords": [{"Value": record.value}],
        },
    }


def _delete_change(row: PlatformDnsRecord) -> dict:
    return {
        "Action": "DELETE",
        "ResourceRecordSet": {
            "Name": naming.denormalize_for_route53(row.record_name),
            "Type": row.record_type,
            "TTL": row.ttl,
            "ResourceRecords": [{"Value": row.record_value}],
        },
    }
