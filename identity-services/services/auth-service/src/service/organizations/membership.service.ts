import { HttpError } from '@launchpad/common';
import { Op, type Transaction } from 'sequelize';
import { Invitation, InvitedUser, Membership, RefreshToken, User } from '@/db';
import { sequelize } from '@/db/sequalize';
import { ORG_ROLE, type OrgRole } from '@/types/org-role';
import { normalizeEmail } from '@/utils/account-id';
import {
    bumpMembershipVersion,
    bumpMembershipVersionForRemoval,
    reviveMembershipVersion,
} from '@/utils/membership-version';
import { assertOrgsEnabled } from './org-guard';
import { recordAuditEvent } from './audit-log.service';
import {
    publishMembershipChanged,
    publishMembershipRemoved,
} from '@/messaging/producer/org-events.message';

// Security review H3(b): RefreshToken.user_id is the legacy credential id (User.id or
// InvitedUser.id), never an Account id — Membership only ever knows the account_id, so
// removal has to map backwards through both credential tables to find every refresh
// token this account could use to keep acting in the org it was just removed from. Best
// effort within the removal's own transaction: if this account is linked to no
// credential row yet (shouldn't happen for a real Membership, but not impossible), this
// is a no-op rather than a failure that would roll back the removal itself.
const revokeRefreshTokensForAccount = async (
    organizationId: string,
    accountId: string,
    transaction: Transaction,
): Promise<void> => {
    const [users, invitedUsers] = await Promise.all([
        User.findAll({
            where: { account_id: accountId },
            attributes: ['id', 'email'],
            transaction,
        }),
        InvitedUser.findAll({
            where: { account_id: accountId },
            attributes: ['id', 'email'],
            transaction,
        }),
    ]);
    const credentials = [...users, ...invitedUsers];
    if (credentials.length === 0) return;

    await RefreshToken.destroy({
        where: { user_id: { [Op.in]: credentials.map((c) => c.id) } },
        transaction,
    });
    // A removed member must not be able to walk back in with an invitation issued before
    // the removal, so every pending invitation to one of their addresses dies with it.
    await Invitation.update(
        { revoked_at: new Date() },
        {
            where: {
                organization_id: organizationId,
                email: { [Op.in]: credentials.map((c) => normalizeEmail(c.email)) },
                accepted_at: null,
                revoked_at: null,
            },
            transaction,
        },
    );
};

export interface AddMemberInput {
    organizationId: string;
    accountId: string;
    role: OrgRole;
    actorAccountId: string | null;
}

// Adds a non-owner member. Ownership is only ever set by createOrganization or (ticket 8)
// a dedicated ownership-transfer transaction — never through this generic path, which is
// the service-level half of the owner invariant (the partial unique index is the DB half).
//
// A removed account re-added to the same org revives its tombstoned row (bumping its
// existing version) rather than creating a second one — the unique (organization_id,
// account_id) index already means there is at most one row per pair regardless of
// removed_at, so "re-add" and "revive" are the same operation.
export const addMemberInTransaction = async (
    input: AddMemberInput,
    transaction: Transaction,
): Promise<Membership> => {
    assertOrgsEnabled();
    if (input.role === ORG_ROLE.OWNER) {
        throw new HttpError(409, 'Cannot add a member as owner; use ownership transfer');
    }

    const existing = await Membership.findOne({
        where: { organization_id: input.organizationId, account_id: input.accountId },
        transaction,
        lock: transaction.LOCK.UPDATE,
    });

    if (existing && existing.removed_at === null) {
        throw new HttpError(409, 'Account is already a member of this organization');
    }

    let result: Membership;
    let action: string;
    if (existing) {
        const newVersion = await reviveMembershipVersion(existing.id, input.role, transaction);
        existing.role = input.role;
        existing.removed_at = null;
        existing.version = newVersion;
        result = existing;
        action = 'membership.revived';
    } else {
        result = await Membership.create(
            {
                organization_id: input.organizationId,
                account_id: input.accountId,
                role: input.role,
            },
            { transaction },
        );
        action = 'membership.added';
    }

    await recordAuditEvent(transaction, {
        organization_id: input.organizationId,
        actor_account_id: input.actorAccountId,
        action,
        target_account_id: input.accountId,
        old_role: null,
        new_role: input.role,
    });
    return result;
};

export const addMember = async (input: AddMemberInput): Promise<Membership> => {
    assertOrgsEnabled();
    if (input.role === ORG_ROLE.OWNER) {
        throw new HttpError(409, 'Cannot add a member as owner; use ownership transfer');
    }

    const membership = await sequelize.transaction((transaction) =>
        addMemberInTransaction(input, transaction),
    );

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
        // removed_at: null — a tombstoned row is not a current member; re-adding it is
        // addMember's revive path, not a role change on something that no longer applies.
        const existing = await Membership.findOne({
            where: {
                organization_id: input.organizationId,
                account_id: input.accountId,
                removed_at: null,
            },
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

// Tombstones (never hard-deletes) the row — see bumpMembershipVersionForRemoval. A hard
// delete would leave no row to reject a stale org.membership.changed redelivered after
// this removal, which could otherwise hand a removed member their role back (the
// resurrection race this ticket closes). Re-adding the same account later is addMember's
// revive path on this same row, never a second row.
export const removeMember = async (input: RemoveMemberInput): Promise<void> => {
    assertOrgsEnabled();

    const removed = await sequelize.transaction(async (transaction) => {
        const existing = await Membership.findOne({
            where: {
                organization_id: input.organizationId,
                account_id: input.accountId,
                removed_at: null,
            },
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

        const { organization_id, account_id, role } = existing;
        const newVersion = await bumpMembershipVersionForRemoval(existing.id, transaction);
        await recordAuditEvent(transaction, {
            organization_id: input.organizationId,
            actor_account_id: input.actorAccountId,
            action: 'membership.removed',
            target_account_id: input.accountId,
            old_role: role,
            new_role: null,
        });
        // Security review H3(b): forces a re-login rather than relying on cache
        // eviction / the refresh-token re-validation in InvitedUserAuthService.refresh
        // alone — the highest-consequence transition (actual removal) doesn't wait for
        // the removed member's next refresh to notice.
        await revokeRefreshTokensForAccount(organization_id, account_id, transaction);
        return { organization_id, account_id, version: newVersion };
    });

    publishMembershipRemoved({
        organization_id: removed.organization_id,
        account_id: removed.account_id,
        version: removed.version,
    });
};
