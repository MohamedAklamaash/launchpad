import { initModels } from '@/db';
import { sequelize, closeDatabase } from '@/db/sequalize';
import { env } from '@/config/env';
import { backfillPersonalOrgs } from '@/service/personal-org-backfill.service';
import {
    InitOrgEventsPublisher,
    closeOrgEventsPublisher,
    orgEventsPublisher,
} from '@/messaging/producer/org-events.message';
import { drainOutboxFully } from '@/service/organizations/org-event-outbox.service';
import { logger } from '@/utils/logger';

// Run by hand after `backfill:accounts`: `ORGS_ENABLED=true pnpm backfill:personal-orgs`
// (append `-- --republish-existing` to re-emit org.created for orgs that already exist,
// for a mirror that was down or not yet consuming). Events go through the outbox; exit
// code 3 means some are still pending. The flag is needed on this process
// only; it does not enable anything on the running service.
const main = async (): Promise<number> => {
    if (!env.ORGS_ENABLED) {
        throw new Error('ORGS_ENABLED must be true for this script (no org events otherwise)');
    }
    await sequelize.authenticate();
    await initModels();
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
    // org.created events sit in the outbox (written with each org). Drain it here on a
    // confirm channel instead of waiting for the service's dispatcher: a row is marked sent
    // only after the broker confirmed it, so any row left pending means "not delivered".
    const pending = await drainOutboxFully();
    if (pending > 0) {
        logger.error(
            { pending },
            'org.created events are still pending in the outbox - the running service will deliver them, or re-run this script',
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
