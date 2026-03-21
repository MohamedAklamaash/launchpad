import 'dotenv/config';
import { createEnv, z } from '@launchpad/common';

const envSchema = z.object({
    NODE_ENV: z.enum(['development', 'test', 'staging', 'production']),
    AUTH_SERVICE_PORT: z.coerce.number().default(3000),
    INTERNAL_API_TOKEN: z.string(),

    DATABASE_USER_NAME: z.string(),
    DATABASE_PASSWORD: z.string(),
    DATABASE_HOST: z.string(),
    DATABASE_PORT: z.coerce.number().default(5432),
    DATABASE_NAME: z.string(),
    DATABASE_SSL: z.coerce.boolean().default(false),
    DATABASE_SSL_REJECT_UNAUTHORIZED: z.coerce.boolean().default(false),

    AUTH_DB_URL: z.url(),

    DB_POOL_MAX: z.coerce.number().default(25),
    DB_POOL_MIN: z.coerce.number().default(2),
    DB_POOL_ACQUIRE_MS: z.coerce.number().default(30_000),
    DB_POOL_IDLE_MS: z.coerce.number().default(10_000),

    JWT_SECRET: z.string(),
    JWT_REFRESH_SECRET: z.string(),
    JWT_EXPIRES_IN: z.string(),
    JWT_REFRESH_EXPIRES_IN: z.string(),

    GITHUB_TOKEN: z.string(),
    GITHUB_CLIENT_ID: z.string(),
    GITHUB_CLIENT_SECRET: z.string(),
    GITHUB_REDIRECT_URI: z.string(),

    REDIS_HOST: z.string(),
    REDIS_PORT: z.coerce.number().default(6379),
    REDIS_PASSWORD: z.string(),
    REDIS_DB: z.coerce.number().default(0),
    REDIS_USERNAME: z.string().optional().default('default'),

    RABBITMQ_URL: z.string(),

    CB_FAILURE_THRESHOLD: z.coerce.number().default(5),
    CB_TIMEOUT_MS: z.coerce.number().default(30_000),
    CB_SUCCESS_THRESHOLD: z.coerce.number().default(2),

    HTTP_REQUEST_TIMEOUT_MS: z.coerce.number().default(5_000),
    AMQP_MAX_RETRIES: z.coerce.number().default(0),
    AMQP_RETRY_DELAY_MS: z.coerce.number().default(1_000),
    AMQP_PREFETCH_COUNT: z.coerce.number().default(10),
});

export const env = createEnv(envSchema, {
    serviceName: 'auth-service',
    source: process.env,
});

export type Env = typeof env;
