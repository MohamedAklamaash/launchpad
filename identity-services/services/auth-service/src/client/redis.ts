import { RedisOptions } from "ioredis";
import { env } from "@/config/env";

export const redisConfig: RedisOptions = {
    host: env.REDIS_HOST,
    port: env.REDIS_PORT,
    password: env.REDIS_PASSWORD,
    db: env.REDIS_DB,
    username: env.REDIS_USERNAME,
    maxRetriesPerRequest: null,
    lazyConnect: true,
    connectTimeout: 5_000,
    commandTimeout: 3_000,
    retryStrategy: (times: number) => {
        if (times > 10) return null;
        return Math.min(times * 200, 3_000);
    },
    reconnectOnError: (err: Error) => {
        return err.message.includes("READONLY");
    },
};
