import { HttpError } from '@launchpad/common';
import type { Transaction } from 'sequelize';
import { Membership, Organization } from '@/db';
import { sequelize } from '@/db/sequalize';
import { ORG_ROLE } from '@/types/org-role';
import type { AccessTokenPayload } from '@/utils/handle-token';
import { bumpMembershipVersion } from '@/utils/membership-version';
import { assertRecentAuth } from '@/utils/reauth';
import { recordAuditEvent } from './audit-log.service';
import { revokeRefreshTokens } from './membership.service';
import { enqueueMembershipChanged } from './org-event-outbox.service';
import { requireOrgOwner } from './org-access.service';
import { assertOrgsEnabled } from './org-guard';
import { hasVerifiedCredential, isPersonalOrganization } from './org-identity';

export interface TransferOwnershipInput {
    caller: AccessTokenPayload;
    organizationId: string;
    newOwnerAccountId: string;
}

export interface TransferOwnershipResult {
    organization_id: string;
    owner_account_id: string;
    previous_owner_account_id: string;
    previous_owner_role: typeof ORG_ROLE.ADMIN;
}

const TARGET_REJECTED =
    'The new owner must be an active member of this organization with a verified account';

const lockActiveMembership = (
    organizationId: string,
    accountId: string,
    transaction: Transaction,
) =>
    Membership.findOne({
        where: { organization_id: organizationId, account_id: accountId, removed_at: null },
        transaction,
        lock: transaction.LOCK.UPDATE,
    });

const lockAndValidate = async (
    input: TransferOwnershipInput,
    ownerAccountId: string,
    t: Transaction,
) => {
    const organization = await Organization.findByPk(input.organizationId, {
        transaction: t,
        lock: t.LOCK.UPDATE,
    });
    if (!organization) throw new HttpError(404, 'Organization not found');
    if (organization.owner_account_id !== ownerAccountId) {
        throw new HttpError(
            409,
            'Organization ownership changed or is inconsistent; reload and retry',
        );
    }
    if (await isPersonalOrganization(input.organizationId, ownerAccountId, t)) {
        throw new HttpError(409, 'A personal organization cannot change owner');
    }

    const currentOwner = await lockActiveMembership(input.organizationId, ownerAccountId, t);
    if (!currentOwner) throw new HttpError(404, 'Organization not found');
    if (currentOwner.role !== ORG_ROLE.OWNER) {
        throw new HttpError(403, 'Only the organization owner can do this');
    }

    const target = await lockActiveMembership(input.organizationId, input.newOwnerAccountId, t);
    if (
        !target ||
        target.role === ORG_ROLE.OWNER ||
        !(await hasVerifiedCredential(input.newOwnerAccountId, t))
    ) {
        throw new HttpError(409, TARGET_REJECTED);
    }
    return { currentOwner, target };
};

// Demote strictly before promote: the partial unique index (one active owner per org) is
// checked per statement, so promoting first would violate it inside the transaction.
const swapOwner = async (
    input: TransferOwnershipInput,
    ownerAccountId: string,
    transaction: Transaction,
): Promise<void> => {
    const { currentOwner, target } = await lockAndValidate(input, ownerAccountId, transaction);
    const targetOldRole = target.role;

    const demotedVersion = await bumpMembershipVersion(
        currentOwner.id,
        ORG_ROLE.ADMIN,
        transaction,
    );
    const promotedVersion = await bumpMembershipVersion(target.id, ORG_ROLE.OWNER, transaction);
    await Organization.update(
        { owner_account_id: input.newOwnerAccountId },
        { where: { id: input.organizationId }, transaction },
    );

    await recordAuditEvent(transaction, {
        organization_id: input.organizationId,
        actor_account_id: ownerAccountId,
        action: 'membership.ownership_demoted',
        target_account_id: ownerAccountId,
        old_role: ORG_ROLE.OWNER,
        new_role: ORG_ROLE.ADMIN,
    });
    await recordAuditEvent(transaction, {
        organization_id: input.organizationId,
        actor_account_id: ownerAccountId,
        action: 'membership.ownership_promoted',
        target_account_id: input.newOwnerAccountId,
        old_role: targetOldRole,
        new_role: ORG_ROLE.OWNER,
    });
    await revokeRefreshTokens(ownerAccountId, transaction);

    // Demote first in the outbox (seq order). The promote also names the account it
    // displaces, so a mirror that never receives the demote still ends with one owner.
    await enqueueMembershipChanged(transaction, {
        organization_id: input.organizationId,
        account_id: ownerAccountId,
        role: ORG_ROLE.ADMIN,
        version: demotedVersion,
    });
    await enqueueMembershipChanged(transaction, {
        organization_id: input.organizationId,
        account_id: input.newOwnerAccountId,
        role: ORG_ROLE.OWNER,
        version: promotedVersion,
        previous_owner_account_id: ownerAccountId,
        previous_owner_version: demotedVersion,
    });
};

// Order of checks: authorize (404 non-member / 403 non-owner), then reauth, then one
// transaction that re-verifies everything under row locks. The pre-transaction owner check
// only rejects early; the locked re-check inside swapOwner is the one that counts.
export const transferOwnership = async (
    input: TransferOwnershipInput,
): Promise<TransferOwnershipResult> => {
    assertOrgsEnabled();
    const owner = await requireOrgOwner(input.caller, input.organizationId);
    assertRecentAuth(input.caller);
    if (input.newOwnerAccountId === owner.accountId) {
        throw new HttpError(409, 'You already own this organization');
    }

    await sequelize.transaction((transaction) => swapOwner(input, owner.accountId, transaction));

    return {
        organization_id: input.organizationId,
        owner_account_id: input.newOwnerAccountId,
        previous_owner_account_id: owner.accountId,
        previous_owner_role: ORG_ROLE.ADMIN,
    };
};
