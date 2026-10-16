import { HttpError } from '@launchpad/common';
import { Membership } from '@/db';
import { sequelize } from '@/db/sequalize';
import { ORG_ROLE, type OrgRole } from '@/types/org-role';
import { bumpMembershipVersion } from '@/utils/membership-version';
import { assertOrgsEnabled } from './org-guard';
import { recordAuditEvent } from './audit-log.service';
import {
    publishMembershipChanged,
    publishMembershipRemoved,
} from '@/messaging/producer/org-events.message';

export interface AddMemberInput {
    organizationId: string;
    accountId: string;
    role: OrgRole;
    actorAccountId: string | null;
}

// Adds a non-owner member. Ownership is only ever set by createOrganization or (ticket 8)
// a dedicated ownership-transfer transaction — never through this generic path, which is
// the service-level half of the owner invariant (the partial unique index is the DB half).
export const addMember = async (input: AddMemberInput): Promise<Membership> => {
    assertOrgsEnabled();
    if (input.role === ORG_ROLE.OWNER) {
        throw new HttpError(409, 'Cannot add a member as owner; use ownership transfer');
    }

    const membership = await sequelize.transaction(async (transaction) => {
        const created = await Membership.create(
            {
                organization_id: input.organizationId,
                account_id: input.accountId,
                role: input.role,
            },
            { transaction },
        );
        await recordAuditEvent(transaction, {
            organization_id: input.organizationId,
            actor_account_id: input.actorAccountId,
            action: 'membership.added',
            target_account_id: input.accountId,
            old_role: null,
            new_role: input.role,
        });
        return created;
    });

    publishMembershipChanged({
        organization_id: membership.organization_id,
        account_id: membership.account_id,
        role: membership.role,
        version: membership.version,
    });

    return membership;
};

export interface ChangeMemberRoleInput {
    organizationId: string;
    accountId: string;
    newRole: OrgRole;
    actorAccountId: string | null;
}

export const changeMemberRole = async (input: ChangeMemberRoleInput): Promise<Membership> => {
    assertOrgsEnabled();
    if (input.newRole === ORG_ROLE.OWNER) {
        throw new HttpError(
            409,
            'Cannot grant owner role via a role change; use ownership transfer',
        );
    }

    const membership = await sequelize.transaction(async (transaction) => {
        const existing = await Membership.findOne({
            where: { organization_id: input.organizationId, account_id: input.accountId },
            transaction,
            lock: transaction.LOCK.UPDATE,
        });
        if (!existing) throw new HttpError(404, 'Membership not found');
        if (existing.role === ORG_ROLE.OWNER) {
            throw new HttpError(409, 'Cannot change the owner role; use ownership transfer');
        }

        const oldRole = existing.role;
        const newVersion = await bumpMembershipVersion(existing.id, input.newRole, transaction);
        await recordAuditEvent(transaction, {
            organization_id: input.organizationId,
            actor_account_id: input.actorAccountId,
            action: 'membership.role_changed',
            target_account_id: input.accountId,
            old_role: oldRole,
            new_role: input.newRole,
        });

        existing.role = input.newRole;
        existing.version = newVersion;
        return existing;
    });

    publishMembershipChanged({
        organization_id: membership.organization_id,
        account_id: membership.account_id,
        role: membership.role,
        version: membership.version,
    });

    return membership;
};

export interface RemoveMemberInput {
    organizationId: string;
    accountId: string;
    actorAccountId: string | null;
}

export const removeMember = async (input: RemoveMemberInput): Promise<void> => {
    assertOrgsEnabled();

    const removed = await sequelize.transaction(async (transaction) => {
        const existing = await Membership.findOne({
            where: { organization_id: input.organizationId, account_id: input.accountId },
            transaction,
            lock: transaction.LOCK.UPDATE,
        });
        if (!existing) throw new HttpError(404, 'Membership not found');
        if (existing.role === ORG_ROLE.OWNER) {
            // Last-owner lockout, the removal-side counterpart to the partial unique
            // index: that index stops a SECOND owner row from ever existing; this stops
            // the ONLY owner from ever being removed. Full reauth-gated ownership
            // transfer is ticket 8 — this is the floor it builds on, not a substitute.
            throw new HttpError(409, 'Cannot remove the organization owner');
        }

        const { organization_id, account_id, role, version } = existing;
        await existing.destroy({ transaction });
        await recordAuditEvent(transaction, {
            organization_id: input.organizationId,
            actor_account_id: input.actorAccountId,
            action: 'membership.removed',
            target_account_id: input.accountId,
            old_role: role,
            new_role: null,
        });
        return { organization_id, account_id, version };
    });

    publishMembershipRemoved({
        organization_id: removed.organization_id,
        account_id: removed.account_id,
        version: removed.version,
    });
};
