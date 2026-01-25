import { Sequelize } from "sequelize";
import { env } from "@/config/env"
import { logger } from "@/utils/logger";


export const sequelize = new Sequelize(env.USER_DB_URL, {
    dialect: "mysql",
    logging: env.NODE_ENV === "development" ? (msg: unknown) => {
        logger.info({ sequelize: msg })
    } : false,
    define: {
        underscored: true,
        freezeTableName: true,
    },
})

export const ConnectToDatabase = async () => {
    try {
        await sequelize.authenticate()
        logger.info("User Service Database is connected successfully")
    } catch (error: unknown) {
        logger.error(error)
        return
    }
}

export const CloseDatabase = async () => {
    try {
        await sequelize.close()
        logger.info("User Service Database connection is closed")
    } catch (error: unknown) {
        logger.error(error)
        return
    }
}