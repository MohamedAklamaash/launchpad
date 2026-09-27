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
from django.db import connection, transaction

from . import naming
from .desired_state import DesiredRecord, compute_desired_state
from .route53_client import get_route53_client

logger = logging.getLogger(__name__)

# Deletes are ordered wildcard -> edge -> validation within a batch purely for audit-log
# readability; Route53 applies one ChangeBatch atomically, so this ordering carries no
# correctness weight on its own — the atomicity is what removes the dangling-window risk a
# sequential per-record delete would have.
_KIND_DELETE_ORDER = {"wildcard": 0, "edge": 1, "validation": 2}


def _acquire_infra_lock(infrastructure_id: str) -> None:
    """Serializes converge_platform_dns per infrastructure across writer replicas: two
    replicas processing a reconcile for the same infra concurrently (e.g. one message still
    in flight when a second is redelivered, or simply two consumer processes) must not both
    decide "edge needs updating" from a stale read and race on the ledger/Route53 write —
    that race is how an edge record ends up pointing at a torn-down ALB. Held for this
    transaction's lifetime, spanning the desired-state read through the ledger write below.

    Postgres only: pg_advisory_xact_lock has no portable equivalent, and the sqlite backend
    used in tests runs single-connection/single-process, where this race cannot occur.
    """
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [str(infrastructure_id)])


def converge_platform_dns(infrastructure_id: str, *, infra_is_mock: bool, dev_mode: bool) -> None:
    route53_error = None

    with transaction.atomic():
        _acquire_infra_lock(infrastructure_id)

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

        # DELETE requires the record set's CURRENT live TTL/values — Route53 rejects a
        # DELETE whose ResourceRecordSet doesn't exactly match what's live, and our
        # ledger's stored value can drift from reality (an out-of-band change, a previous
        # partial failure). A ledger row for a record Route53 no longer has is simply
        # stale: drop it without calling Route53 at all rather than sending a DELETE that
        # would fail and wedge teardown forever on a record that isn't there.
        delete_pairs = []  # (ledger row, the live ResourceRecordSet to delete)
        stale_ledger_ids = []
        for row in to_delete:
            live_record = _fetch_live_record(client, zone_id, row.record_name, row.record_type)
            if live_record is None:
                stale_ledger_ids.append(row.id)
            else:
                delete_pairs.append((row, live_record))

        if stale_ledger_ids:
            PlatformDnsRecord.objects.filter(id__in=stale_ledger_ids).delete()

        for record in to_upsert:
            PlatformDnsRecord.objects.update_or_create(
                infrastructure_id=infrastructure_id,
                record_name=record.name,
                record_type=record.record_type,
                defaults={"kind": record.kind, "record_value": record.value, "ttl": record.ttl},
            )

        # The Route53 call is caught, not left to propagate, so a failure here can never
        # roll back the ledger writes above: they must survive the transaction commit
        # regardless of whether Route53 accepted the batch, exactly as before this
        # function held everything in one transaction (see PlatformDnsRecord's docstring —
        # insert-before-UPSERT is what makes a retry after a crash/failure safe). The
        # error is re-raised once the transaction (and the advisory lock with it) has
        # committed and released.
        changes = [_upsert_change(record) for record in to_upsert] + [
            _delete_change(live_record) for _row, live_record in delete_pairs
        ]
        if changes:
            try:
                client.change_resource_record_sets(HostedZoneId=zone_id, ChangeBatch={"Changes": changes})
            except Exception as exc:
                route53_error = exc

        if route53_error is None and delete_pairs:
            PlatformDnsRecord.objects.filter(id__in=[row.id for row, _live in delete_pairs]).delete()

    if route53_error is not None:
        raise route53_error

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


def _fetch_live_record(client, zone_id: str, name: str, record_type: str) -> dict | None:
    """Targeted lookup for exactly one record — ListResourceRecordSets with
    StartRecordName/StartRecordType/MaxItems=1 returns the first record at or after that
    position, so the result must be checked for an exact name+type match; Route53 has no
    "get one record" API."""
    wire_name = naming.denormalize_for_route53(name)
    response = client.list_resource_record_sets(
        HostedZoneId=zone_id, StartRecordName=wire_name, StartRecordType=record_type, MaxItems="1",
    )
    record_sets = response.get("ResourceRecordSets", [])
    if not record_sets:
        return None
    candidate = record_sets[0]
    if candidate["Name"] != wire_name or candidate["Type"] != record_type:
        return None
    return candidate


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


def _delete_change(live_record: dict) -> dict:
    return {"Action": "DELETE", "ResourceRecordSet": live_record}
