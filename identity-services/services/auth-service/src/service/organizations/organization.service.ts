import { Organization, Membership } from '@/db';
import { sequelize } from '@/db/sequalize';
import { ORG_ROLE } from '@/types/org-role';
import { assertOrgsEnabled } from './org-guard';
import { recordAuditEvent } from './audit-log.service';
import { enqueueOrgCreated } from './org-event-outbox.service';

export interface CreateOrganizationInput {
    ownerAccountId: string;
    name: string;
    // Personal-org backfill (ticket 3+) and the deterministic uuid5(ORG_NAMESPACE,
    // legacy_user_id) id scheme pass this; an organic org created later omits it and gets
    // Organization's own uuidv7 default.
    id?: string;
}

export interface CreateOrganizationResult {
    organization: Organization;
    membership: Membership;
}

// Creates the Organization row and its owner Membership row atomically — the two are
// copies of one fact (plan/F9-teams-and-orgs.md, Decision: Role set) and must never be
// written separately. This is the ONLY place in this service layer allowed to create a
// Membership with role 'owner'; every other mutation (addMember, changeMemberRole) rejects
// that role outright, which is what keeps the partial unique index's invariant from ever
// being tested by application code taking a second path to the same row.
export const createOrganization = async (
    input: CreateOrganizationInput,
): Promise<CreateOrganizationResult> => {
    assertOrgsEnabled();

    const result = await sequelize.transaction(async (transaction) => {
        const organization = await Organization.create(
            { id: input.id, owner_account_id: input.ownerAccountId, name: input.name },
            { transaction },
        );
        const membership = await Membership.create(
            {
                organization_id: organization.id,
                account_id: input.ownerAccountId,
                role: ORG_ROLE.OWNER,
            },
            { transaction },
        );
        await recordAuditEvent(transaction, {
            organization_id: organization.id,
            actor_account_id: null,
            action: 'organization.created',
            target_account_id: input.ownerAccountId,
            old_role: null,
            new_role: ORG_ROLE.OWNER,
        });
        // The outbox row commits with the org, and the dispatcher publishes it only after
        // that commit, so a consumer never sees an event for a rolled-back row.
        await enqueueOrgCreated(transaction, {
            organization_id: organization.id,
            owner_account_id: organization.owner_account_id,
            version: membership.version,
        });
        return { organization, membership };
    });

    return result;
};
