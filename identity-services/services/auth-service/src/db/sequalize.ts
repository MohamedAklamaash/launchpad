import { Sequelize } from "sequelize";
import { env } from "@/config/env"
import { logger } from "@/utils/logger";

export const sequelize = new Sequelize(
    `postgres://${encodeURIComponent(env.DATABASE_USER_NAME)}:` +
    `${encodeURIComponent(env.DATABASE_PASSWORD)}@` +
    `${env.DATABASE_HOST}:${env.DATABASE_PORT}/${env.DATABASE_NAME}`,
    {
        dialect: "postgres",
        dialectOptions: {}, // need to add ssl options here
        logging:
            env.NODE_ENV === "development"
                ? (msg: string) => logger.info({ sequelize: msg })
                : false,
        define: {
            underscored: true,
            freezeTableName: true,
        },
    }
);

export const connectToDatabase = async () => {
    try {
        await sequelize.authenticate()
        logger.info("Auth Database is connected successfully")
    } catch (error: unknown) {
        logger.error(error)
        return
    }
}

export const closeDatabase = async () => {
    try {
        await sequelize.close()
        logger.info("Auth Database connection is closed")
    } catch (error: unknown) {
        logger.error(error)
        return
    }
}