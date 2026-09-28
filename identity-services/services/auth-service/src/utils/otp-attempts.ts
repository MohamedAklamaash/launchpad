import Redis from 'ioredis';
import { redisConfig } from '@/client/redis';

const redis = new Redis(redisConfig);

// A 6-digit OTP has only 1,000,000 possible values — without a cap, an attacker can
// script through them against a single still-valid code. This bounds guesses using the
// same Redis instance already wired for BullMQ, no new infra.
const MAX_ATTEMPTS = 5;
// Matches invited-user.base.service.ts's createOTP expiry — the attempt counter can
// never meaningfully outlive the OTP it's guarding.
const ATTEMPT_WINDOW_SECONDS = 10 * 60;

// Increments KEYS[1] and, only on the very first increment (i.e. the key was absent or
// had just expired), sets its TTL. Both steps run as one atomic unit inside Redis's
// single-threaded script execution — no other client's command can interleave between
// the INCR and the EXPIRE, and no two concurrent callers can ever both observe count==1
// for the same key. This is what makes "claim a slot" safe to call from N concurrent
// requests: each one gets a distinct, strictly increasing count.
const INCR_WITH_WINDOW_SCRIPT = `
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
`;

const incrementWithWindow = async (key: string, windowSeconds: number): Promise<number> =>
    Number(await redis.eval(INCR_WITH_WINDOW_SCRIPT, 1, key, String(windowSeconds)));

export const otpAttempts = {
    // One shared budget per account (or, for an email with no account, per email) —
    // not per purpose. A register OTP and a password-reset OTP outstanding on the same
    // account at once must not double an attacker's guess budget; see B3 in
    // plan/H-hardening.md. Prefer the account id whenever it's known — an email is a
    // caller-supplied string with no canonical casing, while the id is exact.
    keyFor(userId: string | null | undefined, email: string): string {
        return userId ? `otp-attempts:user:${userId}` : `otp-attempts:email:${email.toLowerCase()}`;
    },

    // Atomically claims one attempt slot and reports whether the caller may proceed.
    // MUST be called before the OTP guess is evaluated, not after a failed one:
    // checking "attempts remaining" as a separate read-then-act step lets N requests in
    // flight at once all observe attempts remaining and all get to try a guess before
    // any of them is recorded — this claims the slot unconditionally, whether the guess
    // that follows turns out right or wrong, real account or not.
    async claimAttempt(key: string): Promise<boolean> {
        const count = await incrementWithWindow(key, ATTEMPT_WINDOW_SECONDS);
        return count <= MAX_ATTEMPTS;
    },

    async clearAttempts(key: string): Promise<void> {
        await redis.del(key);
    },

    // Per-email throttle on how often a new reset code can be requested at all — separate
    // from the guess-attempt cap above, which only bounds guesses against a code that
    // already exists. Without this, nothing stops a script from repeatedly calling
    // forgot-password to stockpile outstanding codes or spam the target's inbox. Fixed
    // window counters (1/60s, 5/hour); the caller must skip silently on a throttle hit —
    // this must never change forgot-password's response, which is what keeps it from
    // revealing whether `email` is registered.
    async shouldThrottleForgotPassword(email: string): Promise<boolean> {
        const normalized = email.toLowerCase();
        const [perMinute, perHour] = await Promise.all([
            incrementWithWindow(`forgot-password-throttle:1m:${normalized}`, 60),
            incrementWithWindow(`forgot-password-throttle:1h:${normalized}`, 60 * 60),
        ]);
        return perMinute > 1 || perHour > 5;
    },
};
