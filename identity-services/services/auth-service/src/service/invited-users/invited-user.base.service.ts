import { Transaction } from 'sequelize';
import crypto from 'crypto';
import { RefreshToken, UserOTP } from '@/db';
import { signAccessToken, signRefreshToken, AccessTokenPayload } from '@/utils/handle-token';
import { generateOTP } from '@/utils/generate-otp';
import { personalOrgId } from '@/utils/org-id';
import { env } from '@/config/env';
import { AuthResponse, USER_ROLE, UserData } from '@/types/auth.invited_user.types';
import { OtpPurpose } from '@/types/otp-purpose';

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
            // Security-review fix (H1, revised under R1): an InvitedUser row an inviter
            // created with an attacker-controlled email isn't this person until they
            // prove it by completing OTP verification once. Deliberately NOT
            // `is_authenticated` — that flag is reset to `false` by register() on every
            // subsequent infra invite, which would strip org_id/account_id from an
            // already-verified person's token the moment they're invited anywhere else.
            // Omitted (GitHub `User` rows have no such column — OAuth itself is the
            // proof) reads as verified; explicitly `null` never gets an org_id/
            // account_id claim, no matter what orgContext carries. This is a backstop
            // at the one choke point every login/refresh/switch-org mint goes through —
            // callers must also gate upstream (see InvitedUserAuthService.login).
            email_verified_at?: Date | null;
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
        const verified = user.email_verified_at !== null;
        // Flag-gated here, at the one choke point every login/refresh/switch-org call
        // goes through — with ORGS_ENABLED off, the claims below are never added, so a
        // minted token is byte-for-byte what it was before this ticket (scope item 4).
        // `verified` is the H1 backstop: an unverified InvitedUser never gets org_id or
        // account_id, regardless of what orgContext carries.
        if (env.ORGS_ENABLED && verified && orgContext?.orgId) {
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

    // Deletes any OTP already outstanding for this user+purpose before minting a new
    // one. Without this, repeated calls (a resend, a retried request) stockpile
    // multiple simultaneously-valid codes, which both widens the guessable surface and
    // defeats the point of a single attempt-capped code. A user can still have one
    // register-purpose and one password-reset-purpose code outstanding at the same
    // time; only same-purpose codes are deduped.
    //
    // Security review R2: REGISTER codes used to be additionally scoped to `infra_id`
    // (a user invited to infra A, then invited to infra B before verifying A, kept
    // A's still-valid code instead of having it silently invalidated by B's). That
    // let K simultaneous invites hand an attacker K live codes to guess against at
    // once — the OTP verify lookup (authenticateWithOTP) matches ANY live REGISTER
    // code for the user, not one scoped to a specific infra, so K codes meant a K/
    // 1,000,000 success chance per guess instead of 1/1,000,000. Deduped by
    // (user, purpose) alone now, same as password-reset: at most one live REGISTER
    // code per user, full stop. A new invite to any infra replaces whichever REGISTER
    // code was outstanding, rather than letting both stay valid.
    protected async createOTP(
        userId: string,
        infraId: string,
        purpose: OtpPurpose,
        transaction: Transaction,
    ) {
        await UserOTP.destroy({ where: { invited_user_id: userId, purpose }, transaction });

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
