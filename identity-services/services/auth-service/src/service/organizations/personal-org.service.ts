import { Op, UniqueConstraintError } from 'sequelize';
import { InvitedUser, Membership, Organization, User } from '@/db';
import { ORG_ROLE } from '@/types/org-role';
import { personalOrgId } from '@/utils/org-id';
import { logger } from '@/utils/logger';
import { publishOrgCreated } from '@/messaging/producer/org-events.message';
import { assertOrgsEnabled } from './org-guard';
import { createOrganization } from './organization.service';

const ORG_NAME_MAX_LENGTH = 255;
const FALLBACK_ORG_NAME = 'Personal';

// Always a server-side `users` row: `id` seeds the deterministic org id and `account_id`
// becomes the owner. Never populate either from request data.
export interface PersonalOrgOwner {
    id: string;
    account_id: string | null;
    user_name: string;
}

export type PersonalOrgStatus =
    'created' | 'exists' | 'skipped_unlinked' | 'conflict' | 'shared_account' | 'inconsistent';

export interface EnsurePersonalOrgOptions {
    republishExisting?: boolean;
}

const orgNameFor = (owner: PersonalOrgOwner): string =>
    owner.user_name.trim().slice(0, ORG_NAME_MAX_LENGTH) || FALLBACK_ORG_NAME;

const findOwnerMembership = (organizationId: string, accountId: string) =>
    Membership.findOne({
        where: {
            organization_id: organizationId,
            account_id: accountId,
            role: ORG_ROLE.OWNER,
            removed_at: null,
        },
    });

// An Account linked to more than one credential row is reachable by every holder (the
// token's account_id is all org authz looks at), so it must never own a personal org:
// the second holder could be an InvitedUser whose password someone other than the
// GitHub user chose.
const isHeldByAnotherCredential = async (accountId: string, userId: string) => {
    const [otherUser, invitedUser] = await Promise.all([
        User.findOne({ where: { account_id: accountId, id: { [Op.ne]: userId } } }),
        InvitedUser.findOne({ where: { account_id: accountId } }),
    ]);
    return (otherUser !== null && otherUser.id !== userId) || invitedUser !== null;
};

const classifyExisting = async (
    organization: Organization,
    owner: PersonalOrgOwner & { account_id: string },
    options: EnsurePersonalOrgOptions,
): Promise<PersonalOrgStatus> => {
    if (organization.owner_account_id !== owner.account_id) {
        logger.warn(
            {
                personalOrgConflict: {
                    organizationId: organization.id,
                    userId: owner.id,
                    expectedOwnerAccountId: owner.account_id,
                    actualOwnerAccountId: organization.owner_account_id,
                },
            },
            'Personal org id already exists with a different owner - left untouched, needs manual review',
        );
        return 'conflict';
    }

    const membership = await findOwnerMembership(organization.id, owner.account_id);
    if (!membership) {
        logger.error(
            { personalOrgInconsistent: { organizationId: organization.id, userId: owner.id } },
            'Personal org exists but its owner Membership is missing or removed - left untouched, needs manual review',
        );
        return 'inconsistent';
    }

    if (options.republishExisting) {
        publishOrgCreated({
            organization_id: organization.id,
            owner_account_id: organization.owner_account_id,
            version: membership.version,
        });
    }
    return 'exists';
};

// Ensures the personal Organization (id = uuid5(ORG_NAMESPACE, users.id)) and its owner
// Membership exist for a linked GitHub user. Ownership is only ever written through
// createOrganization, and an existing org is never modified: if it is owned by anyone
// other than this user's own Account it is reported as a conflict and left alone.
export const ensurePersonalOrganization = async (
    owner: PersonalOrgOwner,
    options: EnsurePersonalOrgOptions = {},
): Promise<PersonalOrgStatus> => {
    assertOrgsEnabled();

    if (!owner.account_id) {
        logger.warn(
            { personalOrgSkipped: { userId: owner.id } },
            'User has no linked Account - no personal org until it is linked',
        );
        return 'skipped_unlinked';
    }
    const linkedOwner = { ...owner, account_id: owner.account_id };
    if (await isHeldByAnotherCredential(linkedOwner.account_id, owner.id)) {
        logger.warn(
            { personalOrgSharedAccount: { userId: owner.id, accountId: linkedOwner.account_id } },
            'Account is held by more than one credential row - no personal org, needs manual review',
        );
        return 'shared_account';
    }
    const organizationId = personalOrgId(owner.id);

    const existing = await Organization.findByPk(organizationId);
    if (existing) return classifyExisting(existing, linkedOwner, options);

    try {
        await createOrganization({
            id: organizationId,
            ownerAccountId: linkedOwner.account_id,
            name: orgNameFor(owner),
        });
        return 'created';
    } catch (error) {
        if (!(error instanceof UniqueConstraintError)) throw error;
        const raced = await Organization.findByPk(organizationId);
        if (!raced) throw error;
        return classifyExisting(raced, linkedOwner, options);
    }
};
