import Redis from 'ioredis';
import { QueryTypes } from 'sequelize';
import { redisConfig } from '@/client/redis';
import { sequelize } from '@/db/sequalize';

const redis = new Redis(redisConfig);

const INCR_WITH_WINDOW_SCRIPT = `
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
`;

const incrementWithWindow = async (key: string, windowSeconds: number): Promise<number> =>
    Number(await redis.eval(INCR_WITH_WINDOW_SCRIPT, 1, key, String(windowSeconds)));

// A 6-digit OTP has only 1,000,000 possible values — without a cap, an attacker can
// script through them against a single still-valid code.
export const MAX_OTP_ATTEMPTS = 5;
// Matches invited-user.base.service.ts's createOTP expiry — the attempt counter can
// never meaningfully outlive the OTP it's guarding.
const ATTEMPT_WINDOW_MINUTES = 10;

// A row id that can never belong to a real account (Postgres's nil UUID) — every
// unknown-email OTP lookup is bound to this instead of short-circuiting, so the query
// it runs (and the round trip it takes) has the same shape as a known account's,
// whether or not `email` is actually registered.
export const NIL_INVITED_USER_ID = '00000000-0000-0000-0000-000000000000';

interface ClaimRow {
    id: string;
    email: string;
    user_name: string;
    role: string;
    roles: Record<string, string>;
    infra_id: string[];
    created_at: Date;
    failed_otp_attempts: number;
}

export interface OTPAttemptClaim {
    userId: string;
    email: string;
    user_name: string;
    role: string;
    roles: Record<string, string>;
    infra_id: string[];
    created_at: Date;
    attempts: number;
}

export const otpAttempts = {
    // Atomically claims one guess-attempt slot for the account owning `email` — H7: an
    // outage of Redis (the previous home for this counter) used to fail every OTP
    // login/reset closed; moving the counter into the same Postgres transaction the
    // rest of the flow already depends on removes that separate availability coupling.
    //
    // One statement does two things at once: its WHERE clause is the only place that
    // decides whether `email` is a real account, and its SET clause claims the slot —
    // so the exact same query, with the exact same round trip, runs whether or not the
    // account exists. A zero-row result (no account) means nothing was incremented,
    // simply because there was no row for the UPDATE to touch: unknown emails are never
    // counted, as a direct consequence of ordinary set-based UPDATE semantics rather
    // than a separate existence check the caller has to remember to skip.
    //
    // MUST be called before the OTP guess is evaluated, not after a failed one:
    // checking "attempts remaining" as a separate read-then-act step lets N requests in
    // flight at once all observe attempts remaining and all get to try a guess before
    // any of them is recorded. Because the increment and the window reset happen inside
    // one UPDATE, Postgres's own row lock serializes concurrent claims against the same
    // account — no two callers can ever observe the same resulting count.
    async claimAttempt(email: string): Promise<OTPAttemptClaim | null> {
        const rows = await sequelize.query<ClaimRow>(
            `UPDATE invited_users SET
                failed_otp_attempts = CASE
                    WHEN otp_attempts_window_start IS NULL
                      OR otp_attempts_window_start < NOW() - INTERVAL '${ATTEMPT_WINDOW_MINUTES} minutes'
                    THEN 1
                    ELSE failed_otp_attempts + 1
                END,
                otp_attempts_window_start = CASE
                    WHEN otp_attempts_window_start IS NULL
                      OR otp_attempts_window_start < NOW() - INTERVAL '${ATTEMPT_WINDOW_MINUTES} minutes'
                    THEN NOW()
                    ELSE otp_attempts_window_start
                END
             WHERE email = :email
             RETURNING id, email, user_name, role, roles, infra_id, created_at, failed_otp_attempts`,
            { replacements: { email }, type: QueryTypes.SELECT },
        );
        const row = rows[0];
        if (!row) return null;
        return {
            userId: row.id,
            email: row.email,
            user_name: row.user_name,
            role: row.role,
            roles: row.roles,
            infra_id: row.infra_id,
            created_at: row.created_at,
            attempts: row.failed_otp_attempts,
        };
    },

    async resetAttempts(userId: string): Promise<void> {
        await sequelize.query(
            'UPDATE invited_users SET failed_otp_attempts = 0, otp_attempts_window_start = NULL WHERE id = :userId',
            { replacements: { userId }, type: QueryTypes.UPDATE },
        );
    },

    // Per-email throttle on how often a new reset code can be requested at all — separate
    // from the guess-attempt cap above, which only bounds guesses against a code that
    // already exists. Without this, nothing stops a script from repeatedly calling
    // forgot-password to stockpile outstanding codes or spam the target's inbox. Fixed
    // window counters (1/60s, 5/hour); the caller must skip silently on a throttle hit —
    // this must never change forgot-password's response, which is what keeps it from
    // revealing whether `email` is registered. Kept on Redis (unlike the attempt cap
    // above) and fails OPEN on a Redis error — see requestPasswordReset's call site.
    async shouldThrottleForgotPassword(email: string): Promise<boolean> {
        const normalized = email.toLowerCase();
        const [perMinute, perHour] = await Promise.all([
            incrementWithWindow(`forgot-password-throttle:1m:${normalized}`, 60),
            incrementWithWindow(`forgot-password-throttle:1h:${normalized}`, 60 * 60),
        ]);
        return perMinute > 1 || perHour > 5;
    },
};
