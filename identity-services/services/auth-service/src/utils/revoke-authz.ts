import { HttpError } from '@launchpad/common';
import { verifySessionToken, verifyRefreshToken } from '@/utils/handle-token';

// Resolves the caller's own userId from a verified credential, never from anything the
// request body claims. POST /api/v1/auth/revoke has no path/body parameter for a target
// user — it only ever revokes the caller's own sessions. The access token is accepted
// signature-valid-and-unexpired only (no auth_time freshness check): the dashboard calls
// this from the `reauth_required` interceptor, at the exact moment the access token used
// for the original request failed only a freshness gate elsewhere, not its own validity.
// A refresh token is accepted as a fallback proof of possession for the same caller — and
// is tried whenever the access token is missing or fails its own check, since the
// frontend sends both credentials together and either one proving the same caller is
// enough.
export const resolveRevokeCallerId = (
    authHeader: string | undefined,
    refreshToken: string | undefined,
): string => {
    const accessToken = authHeader?.split(' ')[1];
    if (accessToken) {
        try {
            // verifySessionToken rejects a narrow-scope token (e.g. a password_reset
            // token) the same way it rejects an invalid one — either way, fall through
            // to the refresh token below rather than letting it revoke sessions.
            return verifySessionToken(accessToken).sub;
        } catch {
            // Fall through to the refresh token below.
        }
    }

    if (refreshToken) {
        try {
            return verifyRefreshToken(refreshToken).sub;
        } catch {
            throw new HttpError(401, 'Invalid refresh token');
        }
    }

    throw new HttpError(401, 'Authorization header or refreshToken is required');
};
