import { z } from "zod";

export const resilienceEnvSchema = {
    // DB Pool
    DB_POOL_MIN: z.coerce.number().default(2),
    DB_POOL_MAX: z.coerce.number().default(10),
    DB_POOL_ACQUIRE: z.coerce.number().default(30000),
    DB_POOL_IDLE: z.coerce.number().default(10000),

    // Circuit Breaker
    CB_FAILURE_THRESHOLD: z.coerce.number().default(5),
    CB_SUCCESS_THRESHOLD: z.coerce.number().default(2),
    CB_TIMEOUT: z.coerce.number().default(30000),
    CB_HALF_OPEN_MAX_CALLS: z.coerce.number().default(3),

    // AMQP
    AMQP_RETRY_DELAY: z.coerce.number().default(1000),
    AMQP_MAX_RETRIES: z.coerce.number().default(10),
};
