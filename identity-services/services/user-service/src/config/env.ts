import "dotenv/config"
import { createEnv, z } from "@launchpad/common";

const envSchema = z.object({
    NODE_ENV: z.enum(["development", "test", "staging", "production"]),
    USER_SERVICE_PORT: z.coerce.number().default(3000),
    INTERNAL_API_TOKEN: z.string(),

    USER_DB_URL: z.string(),
    RABBITMQ_URL: z.string(),

    // DB Pool
    DB_POOL_MAX: z.coerce.number().default(25),
    DB_POOL_MIN: z.coerce.number().default(2),
    DB_POOL_ACQUIRE_MS: z.coerce.number().default(30000),
    DB_POOL_IDLE_MS: z.coerce.number().default(10000),

    // Circuit Breaker
    CB_FAILURE_THRESHOLD: z.coerce.number().default(5),
    CB_TIMEOUT_MS: z.coerce.number().default(30000),
    CB_SUCCESS_THRESHOLD: z.coerce.number().default(2),

    // AMQP
    AMQP_MAX_RETRIES: z.coerce.number().default(0),
    AMQP_RETRY_DELAY_MS: z.coerce.number().default(1000),
    AMQP_PREFETCH_COUNT: z.coerce.number().default(10),

    // HTTP Client
    HTTP_REQUEST_TIMEOUT_MS: z.coerce.number().default(5000),
})

export const env = createEnv(envSchema,
    {
        serviceName: "user-service",
        source: process.env
    }
)

export type Env = typeof env;