import { MongoClient } from "mongodb";
import { env } from "@/config/env"
import { logger } from "@/utils/logger";

let mongoClient: MongoClient | null = null

export const ConnectMongoDB = async (): Promise<MongoClient | null> => {
    try {
        if (mongoClient) {
            return mongoClient
        }
        mongoClient = new MongoClient(env.MONGODB_URL, {
            maxPoolSize: env.MONGO_POOL_SIZE,
            connectTimeoutMS: env.MONGO_CONNECT_TIMEOUT_MS,
        })
        await mongoClient.connect()
        logger.info("Connected to Mongodb successfully")
        return mongoClient
    } catch (err: unknown) {
        logger.error("Error connecting to Mongodb successfully")
        throw err;
    }
}

export const CloseMongoConnection = async () => {
    if (!mongoClient) {
        logger.error("Mongo DB Client was not initialized")
        return
    }
    await mongoClient.close()
    return
}