import Redis from 'ioredis';
import { redisConfig } from '@/client/redis';

const redis = new Redis(redisConfig);

// A 6-digit OTP has only 1,000,000 possible values — without a cap, an attacker can
// script through them against a single still-valid code. This bounds guesses per
// email+purpose using the same Redis instance already wired for BullMQ, no new infra.
const MAX_ATTEMPTS = 5;
// Matches invited-user.base.service.ts's createOTP expiry — the attempt counter can
// never meaningfully outlive the OTP it's guarding.
const ATTEMPT_WINDOW_SECONDS = 10 * 60;

const attemptsKey = (purpose: string, email: string) => `otp-attempts:${purpose}:${email}`;

// Exported as a single object (rather than free functions) so tests can `mock.method`
// it the same way they stub `userService`/`notificationService`, without touching the
// real Redis connection.
export const otpAttempts = {
    async hasAttemptsRemaining(purpose: string, email: string): Promise<boolean> {
        const count = await redis.get(attemptsKey(purpose, email));
        return count === null || Number(count) < MAX_ATTEMPTS;
    },

    async recordFailedAttempt(purpose: string, email: string): Promise<void> {
        const key = attemptsKey(purpose, email);
        const count = await redis.incr(key);
        if (count === 1) {
            await redis.expire(key, ATTEMPT_WINDOW_SECONDS);
        }
    },

    async clearAttempts(purpose: string, email: string): Promise<void> {
        await redis.del(attemptsKey(purpose, email));
    },
};
