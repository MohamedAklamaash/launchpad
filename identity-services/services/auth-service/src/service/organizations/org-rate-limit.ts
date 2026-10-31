import { HttpError } from '@launchpad/common';
import { logger } from '@/utils/logger';
import { otpAttempts } from '@/utils/otp-attempts';

export const enforceOrgActionSlot = (
    organizationId: string,
    actorAccountId: string,
): Promise<void> =>
    enforceSlot(() => otpAttempts.consumeOrgActionSlot(organizationId, actorAccountId), {
        tooMany: 'Too many changes to this organization - try again later',
        unavailable: 'Organization changes are temporarily unavailable - try again shortly',
    });

export interface SlotMessages {
    tooMany: string;
    unavailable: string;
}

// Fail closed: an unreachable Redis is not a reason to lift an abuse limit, so a counter
// error refuses the request (503) instead of letting it through.
export const enforceSlot = async (
    consume: () => Promise<boolean>,
    messages: SlotMessages,
): Promise<void> => {
    let allowed: boolean;
    try {
        allowed = await consume();
    } catch (error) {
        logger.error({ err: error }, 'org rate limit check failed, refusing request');
        throw new HttpError(503, messages.unavailable);
    }
    if (!allowed) throw new HttpError(429, messages.tooMany);
};
