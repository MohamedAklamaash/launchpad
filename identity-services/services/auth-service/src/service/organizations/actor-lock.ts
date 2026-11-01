import { HttpError } from '@launchpad/common';
import type { Transaction } from 'sequelize';
import { Membership, Organization } from '@/db';
import { ORG_ROLE, type OrgRole } from '@/types/org-role';

export const MANAGER_ROLES: readonly OrgRole[] = [ORG_ROLE.OWNER, ORG_ROLE.ADMIN];

// Re-checks, inside the mutating transaction, that the actor is still an ACTIVE manager, so
// a concurrently demoted or removed admin cannot land an action that passed the earlier
// read. Lock order is Organization, then the actor row; callers lock the target row after.
// Ownership transfer takes Organization first too, so every path that holds more than one
// membership lock is serialized on the Organization row and cannot deadlock against it.
export const lockManagerActor = async (
    organizationId: string,
    actorAccountId: string,
    transaction: Transaction,
): Promise<OrgRole> => {
    const organization = await Organization.findByPk(organizationId, {
        transaction,
        lock: transaction.LOCK.UPDATE,
    });
    if (!organization) throw new HttpError(404, 'Organization not found');

    const actor = await Membership.findOne({
        where: { organization_id: organizationId, account_id: actorAccountId, removed_at: null },
        transaction,
        lock: transaction.LOCK.UPDATE,
    });
    if (!actor) throw new HttpError(404, 'Organization not found');
    if (!MANAGER_ROLES.includes(actor.role)) {
        throw new HttpError(403, 'Only an owner or admin can do this');
    }
    return actor.role;
};

export const assertOwnerForAdminChange = (
    actorRole: OrgRole | undefined,
    affectedRoles: readonly OrgRole[],
): void => {
    if (!actorRole || actorRole === ORG_ROLE.OWNER) return;
    if (affectedRoles.includes(ORG_ROLE.ADMIN)) {
        throw new HttpError(403, 'Only the organization owner can manage admins');
    }
};
