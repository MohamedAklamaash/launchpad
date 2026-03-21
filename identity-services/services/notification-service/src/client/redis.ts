import { RedisOptions } from 'ioredis';
import { env } from '@/config/env';

export const redisConfig: RedisOptions = {
    host: env.REDIS_HOST,
    port: env.REDIS_PORT,
    password: env.REDIS_PASSWORD,
    db: env.REDIS_DB,
    username: env.REDIS_USERNAME,
    maxRetriesPerRequest: null,
    lazyConnect: true,
    connectTimeout: 10_000,
    commandTimeout: 10_000,
    retryStrategy: (times: number) => {
        if (times > env.REDIS_MAX_RETRIES) return null;
        return Math.min(times * 200, 3_000);
    },
};
