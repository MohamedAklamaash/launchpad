import { HttpError } from '@launchpad/common';
import { env } from '@/config/env';
import type { AccessTokenPayload } from '@/utils/handle-token';

export const REAUTH_ERROR_CODE = 'reauth_required';

// Same allowance as infrastructure-service's exit-export gate (REAUTH_CLOCK_SKEW_SECONDS).
const REAUTH_CLOCK_SKEW_SECONDS = 60;

export class ReauthRequiredError extends HttpError {
    readonly code = REAUTH_ERROR_CODE;

    constructor() {
        super(401, 'Please log in again to continue');
    }
}

// Port of infrastructure-service's `_reauth_ok`. `auth_time` is the original interactive
// login and is never advanced by a refresh. `iat` is deliberately never consulted: every
// refresh mints a new one, so a stolen refresh token could satisfy the check indefinitely.
// A missing or malformed `auth_time` reads as stale, not exempt.
export const assertRecentAuth = (
    payload: Pick<AccessTokenPayload, 'auth_time'>,
    nowSeconds: number = Date.now() / 1000,
): void => {
    const authTime = payload.auth_time;
    if (typeof authTime !== 'number' || !Number.isFinite(authTime)) throw new ReauthRequiredError();
    const age = nowSeconds - authTime;
    if (age < -REAUTH_CLOCK_SKEW_SECONDS || age > env.EXIT_EXPORT_REAUTH_MAX_AGE_SECONDS) {
        throw new ReauthRequiredError();
    }
};
