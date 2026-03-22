import { Sequelize } from 'sequelize';
import { env } from '@/config/env';
import { logger } from '@/utils/logger';

export const sequelize = new Sequelize(
    `postgres://${encodeURIComponent(env.DATABASE_USER_NAME)}:` +
        `${encodeURIComponent(env.DATABASE_PASSWORD)}@` +
        `${env.DATABASE_HOST}:${env.DATABASE_PORT}/${env.DATABASE_NAME}`,
    {
        dialect: 'postgres',
        dialectOptions: {
            connectTimeout: 10_000,
            keepAlive: true,
            statement_timeout: 60_000,
            idle_in_transaction_session_timeout: 30_000,
        },
        logging:
            env.NODE_ENV === 'development'
                ? (msg: string) => logger.info({ sequelize: msg })
                : false,
        define: {
            underscored: true,
            freezeTableName: true,
        },
        pool: {
            max: env.DB_POOL_MAX,
            min: env.DB_POOL_MIN,
            acquire: env.DB_POOL_ACQUIRE_MS,
            idle: env.DB_POOL_IDLE_MS,
            evict: 1_000, // check for idle connections every 1s
        },
    },
);

export const connectToDatabase = async () => {
    try {
        await sequelize.authenticate();
        logger.info('Auth Database is connected successfully');
    } catch (error: unknown) {
        logger.error(error);
        return;
    }
};

export const closeDatabase = async () => {
    try {
        await sequelize.close();
        logger.info('Auth Database connection is closed');
    } catch (error: unknown) {
        logger.error(error);
        return;
    }
};
