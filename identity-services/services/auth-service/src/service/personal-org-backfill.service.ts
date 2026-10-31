import { User } from '@/db';
import { logger } from '@/utils/logger';
import {
    ensurePersonalOrganization,
    type EnsurePersonalOrgOptions,
} from '@/service/organizations/personal-org.service';

export interface PersonalOrgBackfillResult {
    created: number;
    alreadyExisted: number;
    skippedUnlinked: number;
    conflicts: number;
    sharedAccounts: number;
    inconsistent: number;
    failed: number;
}

// Creates the personal Organization + owner Membership for every GitHub `User` with a
// linked Account. `InvitedUser` rows are deliberately not read: infrastructure creation
// is super_admin-only, invited roles are clamped below super_admin, and only GitHub
// `User` rows are created as super_admin, so an invited user never owns an infra.
// Re-runnable: an existing org is skipped, never modified. A per-user failure is
// counted and logged without aborting the pass.
export const backfillPersonalOrgs = async (
    options: EnsurePersonalOrgOptions = {},
): Promise<PersonalOrgBackfillResult> => {
    const users = await User.findAll({
        attributes: ['id', 'account_id', 'user_name'],
        order: [['id', 'ASC']],
    });

    const result: PersonalOrgBackfillResult = {
        created: 0,
        alreadyExisted: 0,
        skippedUnlinked: 0,
        conflicts: 0,
        sharedAccounts: 0,
        inconsistent: 0,
        failed: 0,
    };

    for (const user of users) {
        try {
            const status = await ensurePersonalOrganization(
                { id: user.id, account_id: user.account_id ?? null, user_name: user.user_name },
                options,
            );
            if (status === 'created') result.created += 1;
            else if (status === 'exists') result.alreadyExisted += 1;
            else if (status === 'shared_account') result.sharedAccounts += 1;
            else if (status === 'skipped_unlinked') result.skippedUnlinked += 1;
            else if (status === 'conflict') result.conflicts += 1;
            else result.inconsistent += 1;
        } catch (error) {
            result.failed += 1;
            logger.error({ error, userId: user.id }, 'Personal org backfill failed for user');
        }
    }

    logger.info({ personalOrgBackfill: result }, 'Personal org backfill pass complete');
    return result;
};
