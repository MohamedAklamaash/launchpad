import { initModels } from '@/db';
import { sequelize, closeDatabase } from '@/db/sequalize';
import { env } from '@/config/env';
import { backfillPersonalOrgs } from '@/service/personal-org-backfill.service';
import {
    InitOrgEventsPublisher,
    closeOrgEventsPublisher,
    orgEventsPublisher,
    verifyOrgEventsDelivered,
} from '@/messaging/producer/org-events.message';
import { logger } from '@/utils/logger';

// Run by hand after `backfill:accounts`: `ORGS_ENABLED=true pnpm backfill:personal-orgs`
// (append `-- --republish-existing` to re-emit org.created for orgs that already exist,
// for a mirror that was down or not yet consuming). The flag is needed on this process
// only; it does not enable anything on the running service.
const main = async (): Promise<number> => {
    if (!env.ORGS_ENABLED) {
        throw new Error('ORGS_ENABLED must be true for this script (no org events otherwise)');
    }
    await sequelize.authenticate();
    await initModels();
    orgEventsPublisher.enableConfirms();
    await InitOrgEventsPublisher();
    if (!orgEventsPublisher.isConnected()) {
        throw new Error(
            'org.events publisher is not connected - refusing to create orgs unannounced',
        );
    }

    const result = await backfillPersonalOrgs({
        republishExisting: process.argv.includes('--republish-existing'),
    });
    logger.info({ result }, 'backfill-personal-orgs finished');
    const delivery = await verifyOrgEventsDelivered();
    if (!delivery.delivered) {
        logger.error(
            { delivery },
            'org.created events were not confirmed by the broker - re-run with --republish-existing',
        );
        return 3;
    }
    return result.conflicts + result.sharedAccounts + result.inconsistent + result.failed > 0
        ? 2
        : 0;
};

main()
    .then(async (code) => {
        await closeOrgEventsPublisher();
        await closeDatabase();
        process.exitCode = code;
    })
    .catch(async (error: unknown) => {
        logger.error(error, 'backfill-personal-orgs failed');
        await closeOrgEventsPublisher().catch(() => undefined);
        await closeDatabase();
        process.exitCode = 1;
    });
