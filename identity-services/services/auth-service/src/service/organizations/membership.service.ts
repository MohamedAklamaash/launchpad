import { HttpError } from '@launchpad/common';
import { Op, type Transaction } from 'sequelize';
import { InvitedUser, Membership, RefreshToken, User } from '@/db';
import { sequelize } from '@/db/sequalize';
import { ORG_ROLE, type OrgRole } from '@/types/org-role';
import { normalizeEmail } from '@/utils/account-id';
import {
    bumpMembershipVersion,
    bumpMembershipVersionForRemoval,
    reviveMembershipVersion,
} from '@/utils/membership-version';
import { assertOwnerForAdminChange, lockManagerActor } from './actor-lock';
import { assertOrgsEnabled } from './org-guard';
import { recordAuditEvent, type AuditAction } from './audit-log.service';
import { reportOwnerRoleAttempt } from './owner-attempt-signal';
import { revokePendingInvitations } from './pending-invitation-revocation';
import { enqueueMembershipChanged, enqueueMembershipRemoved } from './org-event-outbox.service';

// Security review H3(b): RefreshToken.user_id is the legacy credential id (User.id or
// InvitedUser.id), never an Account id — Membership only ever knows the account_id, so
// revocation has to map backwards through both credential tables to find every refresh
// token this account could use. Best effort within the caller's own transaction: if this
// account is linked to no credential row yet (shouldn't happen for a real Membership, but
// not impossible), this is a no-op rather than a failure that would roll back the caller.
export const revokeRefreshTokens = async (
    accountId: string,
    transaction: Transaction,
): Promise<{ id: string; email: string }[]> => {
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
    if (credentials.length === 0) return [];

    await RefreshToken.destroy({
        where: { user_id: { [Op.in]: credentials.map((c) => c.id) } },
        transaction,
    });
    return credentials;
};

// A removed member must not be able to walk back in with an invitation issued before the
// removal, so every pending invitation to one of their addresses dies with it. Each one
// gets its own invitation.revoked audit row, the same as a manual revoke.
const revokeAccessForRemovedAccount = async (
    organizationId: string,
    accountId: string,
    actorAccountId: string | null,
    transaction: Transaction,
): Promise<void> => {
    const credentials = await revokeRefreshTokens(accountId, transaction);
    if (credentials.length === 0) return;

    await revokePendingInvitations(
        {
            organizationId,
            emails: credentials.map((c) => normalizeEmail(c.email)),
            actorAccountId,
        },
        transaction,
    );
};

export interface AddMemberInput {
    organizationId: string;
    accountId: string;
    role: OrgRole;
    actorAccountId: string | null;
}

// Adds a non-owner member. Ownership is only ever set by createOrganization or
// ownership-transfer.service.ts — never through this generic path, which is
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
        reportOwnerRoleAttempt({
            reason: 'owner_role_requested',
            organization_id: input.organizationId,
            actor_account_id: input.actorAccountId,
            target_account_id: input.accountId,
        });
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
    let action: AuditAction;
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
        reportOwnerRoleAttempt({
            reason: 'owner_role_requested',
            organization_id: input.organizationId,
            actor_account_id: input.actorAccountId,
            target_account_id: input.accountId,
        });
        throw new HttpError(409, 'Cannot add a member as owner; use ownership transfer');
    }

    return sequelize.transaction(async (transaction) => {
        const membership = await addMemberInTransaction(input, transaction);
        await enqueueMembershipChanged(transaction, {
            organization_id: membership.organization_id,
            account_id: membership.account_id,
            role: membership.role,
            version: membership.version,
        });
        return membership;
    });
};

export interface ChangeMemberRoleInput {
    organizationId: string;
    accountId: string;
    newRole: OrgRole;
    actorAccountId: string | null;
    lockActorAsManager?: boolean;
}

export const changeMemberRole = async (input: ChangeMemberRoleInput): Promise<Membership> => {
    assertOrgsEnabled();
    if (input.newRole === ORG_ROLE.OWNER) {
        reportOwnerRoleAttempt({
            reason: 'owner_role_requested',
            organization_id: input.organizationId,
            actor_account_id: input.actorAccountId,
            target_account_id: input.accountId,
        });
        throw new HttpError(
            409,
            'Cannot grant owner role via a role change; use ownership transfer',
        );
    }

    const membership = await sequelize.transaction(async (transaction) => {
        let actorRole: OrgRole | undefined;
        if (input.lockActorAsManager && input.actorAccountId) {
            actorRole = await lockManagerActor(
                input.organizationId,
                input.actorAccountId,
                transaction,
            );
        }
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
            reportOwnerRoleAttempt({
                reason: 'target_is_owner',
                organization_id: input.organizationId,
                actor_account_id: input.actorAccountId,
                target_account_id: input.accountId,
            });
            throw new HttpError(409, 'Cannot change the owner role; use ownership transfer');
        }
        assertOwnerForAdminChange(actorRole, [existing.role, input.newRole]);

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
        await enqueueMembershipChanged(transaction, {
            organization_id: existing.organization_id,
            account_id: existing.account_id,
            role: existing.role,
            version: existing.version,
        });
        return existing;
    });

    return membership;
};

export interface RemoveMemberInput {
    organizationId: string;
    accountId: string;
    actorAccountId: string | null;
    lockActorAsManager?: boolean;
}

// Tombstones (never hard-deletes) the row — see bumpMembershipVersionForRemoval. A hard
// delete would leave no row to reject a stale org.membership.changed redelivered after
// this removal, which could otherwise hand a removed member their role back (the
// resurrection race this ticket closes). Re-adding the same account later is addMember's
// revive path on this same row, never a second row.
export const removeMember = async (input: RemoveMemberInput): Promise<void> => {
    assertOrgsEnabled();

    await sequelize.transaction(async (transaction) => {
        let actorRole: OrgRole | undefined;
        if (input.lockActorAsManager && input.actorAccountId) {
            actorRole = await lockManagerActor(
                input.organizationId,
                input.actorAccountId,
                transaction,
            );
        }
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
            // the ONLY owner from ever being removed. The owner leaves the role only
            // through the reauth-gated ownership transfer.
            throw new HttpError(409, 'Cannot remove the organization owner');
        }
        assertOwnerForAdminChange(actorRole, [existing.role]);

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
        await revokeAccessForRemovedAccount(
            organization_id,
            account_id,
            input.actorAccountId,
            transaction,
        );
        await enqueueMembershipRemoved(transaction, {
            organization_id,
            account_id,
            version: newVersion,
        });
    });
};
