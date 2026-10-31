from api.models.membership import Membership
from api.models.organization import Organization
from django.utils import timezone
from shared.enums.org_role import OrgRole


class PendingOrganization(Exception):
    """The Organization this Membership event belongs to isn't materialized in the
    read-model yet — org.created and the membership events that follow it race on
    publish order the same way infrastructure.created/auth.user.registered do (see
    api/repositories/user.py's PendingInvitedInfrastructure in application-service). The
    caller should retry the event (bounded) rather than drop it."""

    def __init__(self, organization_id):
        self.organization_id = organization_id
        super().__init__(f"organization not yet materialized: {organization_id}")


class OrganizationRepository:
    """Version-rejecting upserts for the org.events read-model mirror (F9 ticket 4). Every
    write here is idempotent on (entity, version): a payload whose version is not strictly
    greater than what's already stored is a silent no-op, never an error — redelivery and
    out-of-order delivery are both expected, not exceptional."""

    def apply_org_created(self, organization_id, owner_account_id, version: int):
        """org.created materializes both the Organization row and its owner Membership —
        auth-service publishes only org.created for the owner, never a separate
        org.membership.changed (organization.service.ts's createOrganization)."""
        self._upsert_organization(organization_id, owner_account_id, version)
        self._upsert_membership(organization_id, owner_account_id, OrgRole.OWNER, version)
        self._sync_owner_account(organization_id)

    def upsert_membership(self, organization_id, account_id, role: str, version: int):
        if not Organization.objects.filter(id=organization_id).exists():
            raise PendingOrganization(organization_id)
        self._upsert_membership(organization_id, account_id, role, version)
        self._sync_owner_account(organization_id)

    def remove_membership(self, organization_id, account_id, version: int):
        if not Organization.objects.filter(id=organization_id).exists():
            raise PendingOrganization(organization_id)
        # Tombstones (never hard-deletes) — a hard delete would leave no row for a stale,
        # redelivered org.membership.changed to be rejected against, letting it recreate
        # the row and hand a removed member their role back. Applied only when the stored
        # row is older than this removal — a removal that arrives stale relative to a
        # newer role change (or a newer removal) already applied must not clobber it. No
        # matching row (never materialized, or already tombstoned at this version or
        # later) is an idempotent no-op.
        Membership.objects.filter(
            organization_id=organization_id, account_id=account_id, version__lt=version,
        ).update(removed_at=timezone.now(), version=version)
        self._sync_owner_account(organization_id)

    @staticmethod
    def _sync_owner_account(organization_id):
        """Keeps Organization.owner_account_id equal to the one active owner Membership.

        An ownership transfer arrives as two independent membership events (each with its
        own per-membership version), and Organization.version is the version of whichever
        membership rode org.created, so it cannot order a second transfer. Deriving the
        column from the membership rows makes it order-independent. With zero owners (the
        gap between the demote and the promote) or two (never expected), the column is left
        as it was."""
        owners = list(
            Membership.objects.active()
            .filter(organization_id=organization_id, role=OrgRole.OWNER)
            .values_list("account_id", flat=True)[:2]
        )
        if len(owners) == 1:
            Organization.objects.filter(pk=organization_id).exclude(
                owner_account_id=owners[0],
            ).update(owner_account_id=owners[0])

    @staticmethod
    def _upsert_organization(organization_id, owner_account_id, version: int):
        org, created = Organization.objects.get_or_create(
            id=organization_id,
            defaults={"owner_account_id": owner_account_id, "version": version},
        )
        if not created:
            Organization.objects.filter(pk=org.pk, version__lt=version).update(
                owner_account_id=owner_account_id, version=version,
            )

    @staticmethod
    def _upsert_membership(organization_id, account_id, role: str, version: int):
        # get_or_create looks up by (organization_id, account_id) alone, tombstoned or
        # not — there is at most one row per pair regardless of removed_at (removal never
        # deletes it), so a re-add after a removal revives this same row rather than
        # colliding with the unique_together constraint on a second one.
        membership, created = Membership.objects.get_or_create(
            organization_id=organization_id, account_id=account_id,
            defaults={"role": role, "version": version},
        )
        if not created:
            # version__lt in the filter, not a precomputed boolean: a lost race between two
            # concurrent writers resolves itself here (SQL evaluates version__lt against the
            # row as committed, not a stale in-memory read), no select_for_update needed.
            # removed_at=None unconditionally: this is also the revive path for a
            # tombstoned row (a re-add is just another org.membership.changed/
            # org.invitation.accepted with a higher version), and it's already None on an
            # active row, so clearing it here is never wrong.
            Membership.objects.filter(pk=membership.pk, version__lt=version).update(
                role=role, version=version, removed_at=None,
            )
