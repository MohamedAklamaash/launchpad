import type { NextFunction, Request, Response } from 'express';
import { getAuthHeader } from '@/utils/auth-header';
import { logger } from '@/utils/logger';
import { verifySessionToken } from '@/utils/handle-token';

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

// Runs before body validation, which would otherwise answer 422 for role=owner without
// anyone noticing. Only a request with a validly signed session is reported, so an
// unauthenticated caller cannot fill the signal with noise. It never blocks the request.
export const flagOwnerRoleRequest = (req: Request, _res: Response, next: NextFunction): void => {
    const role = (req.body as { role?: unknown } | undefined)?.role;
    if (typeof role === 'string' && role.toLowerCase() === 'owner') {
        try {
            const session = verifySessionToken(getAuthHeader(req));
            reportOwnerRoleAttempt({
                reason: 'owner_role_requested',
                organization_id: req.params.id as string,
                actor_account_id: session.account_id ?? null,
                target_account_id: (req.params.accountId as string | undefined) ?? null,
            });
        } catch {
            // Not a valid session: the route's own auth answers it.
        }
    }
    next();
};
