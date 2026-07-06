import 'dotenv/config';
import { createEnv, z } from '@launchpad/common';

const envSchema = z.object({
    NODE_ENV: z.enum(['development', 'test', 'staging', 'production']),
    NOTIFICATION_SERVICE_PORT: z.coerce.number().default(3000),
    INTERNAL_API_TOKEN: z.string(),

    MAIL_HOST: z.string().default('smtp.gmail.com'),
    MAIL_PORT: z.coerce.number().default(587),
    MAIL_USER: z.string(),
    MAIL_APP_PASSWORD: z.string(),
    FROM_MAIL: z.string(),

    REDIS_HOST: z.string(),
    REDIS_PORT: z.coerce.number().default(6379),
    REDIS_PASSWORD: z.string(),
    REDIS_DB: z.coerce.number().default(0),
    REDIS_USERNAME: z.string().optional().default('default'),

    MONGODB_URL: z.string(),
    JWT_SECRET: z.string(),

    MONGO_POOL_SIZE: z.coerce.number().default(10),
    MONGO_CONNECT_TIMEOUT_MS: z.coerce.number().default(30000),

    REDIS_MAX_RETRIES: z.coerce.number().default(10),

    CB_FAILURE_THRESHOLD: z.coerce.number().default(5),
    CB_TIMEOUT_MS: z.coerce.number().default(30000),
    CB_SUCCESS_THRESHOLD: z.coerce.number().default(2),

    HTTP_REQUEST_TIMEOUT_MS: z.coerce.number().default(5000),

    AUTH_SERVICE_URL: z.string(),
    GATEWAY_SERVICE_URL: z.string(),
    // Public dashboard URL for CTA buttons in lifecycle emails. Optional: when unset, emails
    // simply omit the button rather than link somewhere broken.
    DASHBOARD_URL: z.string().url().optional(),
});

export const env = createEnv(envSchema, {
    serviceName: 'notification-service',
    source: process.env,
});

export type Env = typeof env;
