import { initModels } from '@/db';
import { sequelize, closeDatabase } from '@/db/sequalize';
import { backfillAccounts } from '@/service/account-backfill.service';
import { logger } from '@/utils/logger';

// F9 ticket 1: one-off (but idempotent, re-runnable) backfill for the new `accounts` table.
// Run with `pnpm backfill:accounts` from auth-service. Not wired into any request path or
// boot sequence — operators run it by hand after deploying the migration in db/index.ts.
const main = async () => {
    await sequelize.authenticate();
    await initModels();
    const result = await backfillAccounts();
    logger.info({ result }, 'backfill-accounts finished');
};

main()
    .then(() => closeDatabase())
    .catch(async (error: unknown) => {
        logger.error(error, 'backfill-accounts failed');
        await closeDatabase();
        process.exitCode = 1;
    });
