import { Transaction } from 'sequelize';
import crypto from 'crypto';
import { RefreshToken, UserOTP } from '@/db';
import { signAccessToken, signRefreshToken, AccessTokenPayload } from '@/utils/handle-token';
import { generateOTP } from '@/utils/generate-otp';
import { personalOrgId } from '@/utils/org-id';
import { env } from '@/config/env';
import { AuthResponse, USER_ROLE, UserData } from '@/types/auth.invited_user.types';
import { OTP_PURPOSE, OtpPurpose } from '@/types/otp-purpose';

// F9 ticket 5: the org context buildAuthResponse stamps onto a minted access token.
// Both fields are only ever read when ORGS_ENABLED — see buildAuthResponse — but kept as
// a plain object (not flag-gated at the call site) so every caller can pass whatever it
// has in hand without its own env check.
export interface AuthOrgContext {
    accountId?: string | null;
    orgId?: string | null;
}

export abstract class BaseService {
    // `activeOrgId`, when given, is stamped straight onto the new row (switch-org's
    // rotation path). Omitted, it defaults to the caller's personal org — computed with
    // no DB lookup, same formula the personal-org backfill used — so every refresh token
    // this service ever mints already has a sticky, correct `active_org_id` from the
    // moment this column exists, with no separate backfill migration required.
    //
    // Flag-gated: with ORGS_ENABLED off this never computes or stores anything here,
    // both so a flag-off deployment is byte-for-byte unchanged (scope item 4) and so a
    // non-UUID test/legacy id never has to round-trip through personalOrgId's UUID
    // validation when nothing will ever read the result.
    protected async createRefreshToken(
        userId: string,
        transaction: Transaction,
        activeOrgId?: string,
    ) {
        const expiresAt = new Date();
        expiresAt.setDate(expiresAt.getDate() + 30);

        return RefreshToken.create(
            {
                user_id: userId,
                token_id: crypto.randomUUID(),
                expires_at: expiresAt,
                active_org_id: env.ORGS_ENABLED ? (activeOrgId ?? personalOrgId(userId)) : null,
            },
            { transaction },
        );
    }

    protected buildAuthResponse(
        user: {
            id: string;
            email: string;
            user_name: string;
            role: string | USER_ROLE;
            roles?: Record<string, string>;
            infra_id: string[];
            created_at?: Date;
            profile_url?: string;
        },
        refreshTokenId: string,
        // The session's original interactive-login time, unix seconds. Omitted (or
        // undefined) means "this call IS the interactive login" — every call site that
        // authenticates the user directly (password, OTP, GitHub OAuth) leaves this unset
        // and gets `now`. InvitedUserAuthService.refresh is the one caller that must pass
        // the value read off the incoming refresh token, never a fresh timestamp.
        authTime?: number,
        orgContext?: AuthOrgContext,
    ): AuthResponse {
        const auth_time = authTime ?? Math.floor(Date.now() / 1000);
        const tokenClaims: AccessTokenPayload = {
            sub: user.id,
            email: user.email,
            user_name: user.user_name,
            role: user.role,
            roles: user.roles,
            auth_time,
        };
        // Flag-gated here, at the one choke point every login/refresh/switch-org call
        // goes through — with ORGS_ENABLED off, the claims below are never added, so a
        // minted token is byte-for-byte what it was before this ticket (scope item 4).
        if (env.ORGS_ENABLED && orgContext?.orgId) {
            tokenClaims.org_id = orgContext.orgId;
            // Omitted entirely (not sent as null/empty) when the caller's credential row
            // has no linked Account yet — a consumer must be able to tell "no claim" apart
            // from "empty claim" (handle-token.ts's AccessTokenPayload.account_id).
            if (orgContext.accountId) {
                tokenClaims.account_id = orgContext.accountId;
            }
        }
        return {
            user: {
                id: user.id,
                email: user.email,
                user_name: user.user_name,
                role: user.role as USER_ROLE,
                roles: user.roles,
                infra_id: user.infra_id || [],
                createdAt: user.created_at
                    ? user.created_at.toISOString()
                    : new Date().toISOString(),
                profile_url: user.profile_url,
            } as UserData,
            accessToken: signAccessToken(tokenClaims),
            refreshToken: signRefreshToken({ sub: user.id, tokenId: refreshTokenId, auth_time }),
            access_token: signAccessToken(tokenClaims),
            refresh_token: signRefreshToken({ sub: user.id, tokenId: refreshTokenId, auth_time }),
        };
    }

    // Deletes any OTP already outstanding for this user+purpose (register: also scoped
    // to this infra) before minting a new one. Without this, repeated calls (a resend,
    // a retried request) stockpile multiple simultaneously-valid codes, which both
    // widens the guessable surface and defeats the point of a single attempt-capped
    // code. A user can still have one register-purpose and one password-reset-purpose
    // code outstanding at the same time; only same-purpose codes are deduped.
    //
    // Register codes are additionally scoped to `infra_id`: a user invited to infra A,
    // then invited to infra B before verifying A's still-valid invite, must not have
    // A's code silently invalidated by B's — each infra's invite is its own flow with
    // its own code. Password-reset stays deduped by (user, purpose) alone — resetting
    // is a single per-account flow, not a per-infra one, and requestPasswordReset picks
    // one infra to stamp the reset token's context with regardless of how many the
    // account belongs to. The attempt-cap budget stays shared across every live code
    // for the account (see otp-attempts.ts) either way, so this widens which code can
    // be *outstanding*, never how many guesses an attacker gets against the account.
    protected async createOTP(
        userId: string,
        infraId: string,
        purpose: OtpPurpose,
        transaction: Transaction,
    ) {
        const dedupWhere =
            purpose === OTP_PURPOSE.REGISTER
                ? { invited_user_id: userId, purpose, infra_id: infraId }
                : { invited_user_id: userId, purpose };
        await UserOTP.destroy({ where: dedupWhere, transaction });

        const expiresAt = new Date();
        expiresAt.setMinutes(expiresAt.getMinutes() + 10); // 10 min expiry window
        return UserOTP.create(
            {
                invited_user_id: userId,
                otp: generateOTP(),
                expires_at: expiresAt,
                infra_id: infraId,
                purpose,
            },
            { transaction },
        );
    }
}
