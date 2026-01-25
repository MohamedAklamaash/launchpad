import { RedisOptions } from "ioredis";
import { env } from "@/config/env";

export const redisConfig: RedisOptions = {
    host: env.REDIS_HOST,
    port: env.REDIS_PORT,
    password: env.REDIS_PASSWORD,
    db: env.REDIS_DB,
    username: env.REDIS_USERNAME,
    maxRetriesPerRequest: null,
};
