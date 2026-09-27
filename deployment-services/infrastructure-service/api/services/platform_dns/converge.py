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

F1b part 3a: `synced_at` (part of the host-URL publish gate — see host_readiness.py) is
populated here from Route53's own GetChange status, never assumed. A change that lands
already INSYNC (every real UPSERT/DELETE that requires no propagation, and the mock zone
unconditionally) is stamped immediately. One still PENDING is left with its `change_id`
recorded and `synced_at` null — converge_platform_dns itself never sleeps waiting for it
(RECOMMENDED item 1, security review: this runs on the writer's single prefetch_count=1
consumer thread, and a real Route53 propagation wait there would delay every other
message behind it, including a teardown reconcile). Two catch-up paths confirm it later
instead: `_stamp_pending_syncs`, a one-shot no-sleep GetChange run at the start of the
*next* reconcile for this same infrastructure, and `sweep_pending_dns_syncs`
(management command `sync_platform_dns_changes`), an out-of-process periodic sweep over
every infra's pending rows — see that command's docstring for why it's a separate
scheduled job rather than a thread inside the consumer, mirroring `sweep_platform_dns`'s
own operational pattern.
"""
import logging

from api.models.platform_dns_record import PlatformDnsRecord
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

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
    client = None
    zone_id = None
    to_upsert: list[DesiredRecord] = []
    to_delete: list = []

    def _client():
        nonlocal client, zone_id
        if client is None:
            client, zone_id = get_route53_client(infra_is_mock=infra_is_mock, dev_mode=dev_mode)
        return client, zone_id

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

        # Catch up any row from a previous UPSERT that never got confirmed INSYNC — a
        # redelivered message, or one whose earlier poll (below) ran out of attempts.
        # One GetChange per row, no sleep: cheap, and the bounded poll below still runs for
        # anything issued by *this* invocation.
        pending_sync_rows = [row for row in ledger_by_key.values() if row.synced_at is None and row.change_id]
        if pending_sync_rows:
            _stamp_pending_syncs(_client()[0], pending_sync_rows)

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

        client, zone_id = _client()

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
                defaults={
                    "kind": record.kind, "record_value": record.value, "ttl": record.ttl,
                    "synced_at": None, "change_id": None,
                },
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
        change_id = None
        change_status = None
        if changes:
            try:
                response = client.change_resource_record_sets(HostedZoneId=zone_id, ChangeBatch={"Changes": changes})
                change_info = response.get("ChangeInfo", {})
                change_id = change_info.get("Id")
                change_status = change_info.get("Status")
            except Exception as exc:
                route53_error = exc

        if route53_error is None:
            if delete_pairs:
                PlatformDnsRecord.objects.filter(id__in=[row.id for row, _live in delete_pairs]).delete()
            if to_upsert and change_id:
                # RECOMMENDED item 5 (security review): exact (name, type) pairs, not an
                # __in/__in cross product — record_type is "CNAME" for everything this
                # writer creates today, so the old filter happened to be exact in practice,
                # but a name shared across two different types (or vice versa) would have
                # matched rows outside this upsert's own set.
                pair_condition = Q()
                for record in to_upsert:
                    pair_condition |= Q(record_name=record.name, record_type=record.record_type)
                q = PlatformDnsRecord.objects.filter(pair_condition, infrastructure_id=infrastructure_id)
                if change_status == "INSYNC":
                    q.update(synced_at=timezone.now(), change_id=None)
                else:
                    q.update(change_id=change_id)

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


def _stamp_pending_syncs(client, rows: list) -> None:
    """One GetChange per row still awaiting confirmation, no sleep — a cheap catch-up for
    whatever a previous invocation's bounded poll didn't resolve in time. Any error (e.g. a
    transient Route53 blip) is swallowed per-row: this is a best-effort nicety, and the same
    catch-up runs again on the next reconcile regardless."""
    for row in rows:
        try:
            status = client.get_change(Id=row.change_id)["ChangeInfo"]["Status"]
        except Exception:
            logger.warning("platform DNS GetChange catch-up failed for %s (will retry next reconcile)",
                            row.change_id, exc_info=True)
            continue
        if status == "INSYNC":
            # Guarded on change_id (RECOMMENDED item 5): if a concurrent reconcile already
            # re-upserted this row with a different change_id since it was read above, this
            # is now stale information and must not stamp synced_at for the wrong change.
            PlatformDnsRecord.objects.filter(id=row.id, change_id=row.change_id).update(
                synced_at=timezone.now(), change_id=None,
            )


def sweep_pending_dns_syncs(client) -> int:
    """Out-of-process periodic catch-up for every infra's pending rows — see
    api/management/commands/sync_platform_dns_changes.py, the scheduled job this backs
    (RECOMMENDED item 1, security review). Deliberately not run from inside the
    message-consuming process: converge_platform_dns never sleeps waiting for INSYNC (see
    this module's docstring), and calling this from the same process on a timer would only
    reintroduce the same "blocks the single prefetch_count=1 consumer thread" problem for
    however long a batch of GetChange calls takes.

    Groups pending rows by change_id first so a single Route53 ChangeBatch that touched
    several records (e.g. an edge+wildcard pair from one reconcile) costs one GetChange
    call, not one per row. Returns the number of rows newly confirmed INSYNC.
    """
    pending = list(
        PlatformDnsRecord.objects.filter(synced_at__isnull=True).exclude(change_id__isnull=True).exclude(change_id="")
    )
    rows_by_change_id: dict[str, list] = {}
    for row in pending:
        rows_by_change_id.setdefault(row.change_id, []).append(row)

    confirmed = 0
    for change_id, rows in rows_by_change_id.items():
        try:
            status = client.get_change(Id=change_id)["ChangeInfo"]["Status"]
        except Exception:
            logger.warning(
                "sync_platform_dns_changes: GetChange failed for %s (will retry next run)",
                change_id, exc_info=True,
            )
            continue
        if status != "INSYNC":
            continue
        # Guarded on change_id (RECOMMENDED item 5): a row a concurrent reconcile has
        # since re-upserted with a *different* change_id must not be stamped synced for
        # a change that no longer describes its current value.
        updated = PlatformDnsRecord.objects.filter(
            id__in=[row.id for row in rows], change_id=change_id,
        ).update(synced_at=timezone.now(), change_id=None)
        confirmed += updated
    return confirmed


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
