import { logger } from '@/utils/logger';

export type OwnerAttemptReason =
    'owner_role_requested' | 'target_is_owner' | 'transfer_by_non_owner';

export interface OwnerAttempt {
    reason: OwnerAttemptReason;
    organization_id?: string;
    actor_account_id?: string | null;
    target_account_id?: string | null;
}

// The alert signal for "someone tried to touch the owner role through a path that must
// never allow it". Warn level and a fixed event name so one log query can page on it. Ids
// only: no email, token or name.
export const reportOwnerRoleAttempt = (attempt: OwnerAttempt): void => {
    logger.warn({ event: 'org_owner_role_change_attempt', ...attempt }, 'owner role attempt');
};
