from shared.enums.org_role import OrgRole

from api.models.membership import Membership
from api.models.organization import Organization


class PendingOrganization(Exception):
    """The Organization this Membership event belongs to isn't materialized in the
    read-model yet — org.created and the membership events that follow it race on
    publish order, the same way infrastructure.created/auth.user.registered do in the
    other two services' consumers. The caller should retry the event (bounded) rather
    than drop it."""

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

    def upsert_membership(self, organization_id, account_id, role: str, version: int):
        if not Organization.objects.filter(id=organization_id).exists():
            raise PendingOrganization(organization_id)
        self._upsert_membership(organization_id, account_id, role, version)

    def remove_membership(self, organization_id, account_id, version: int):
        if not Organization.objects.filter(id=organization_id).exists():
            raise PendingOrganization(organization_id)
        # Deletes only when the stored row is older than this removal — a removal that
        # arrives stale relative to a newer role change already applied must not clobber
        # it. No matching row (already removed, or never existed) is an idempotent no-op.
        Membership.objects.filter(
            organization_id=organization_id, account_id=account_id, version__lt=version,
        ).delete()

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
        membership, created = Membership.objects.get_or_create(
            organization_id=organization_id, account_id=account_id,
            defaults={"role": role, "version": version},
        )
        if not created:
            # version__lt in the filter, not a precomputed boolean: a lost race between two
            # concurrent writers resolves itself here (SQL evaluates version__lt against the
            # row as committed, not a stale in-memory read), no select_for_update needed.
            Membership.objects.filter(pk=membership.pk, version__lt=version).update(
                role=role, version=version,
            )
