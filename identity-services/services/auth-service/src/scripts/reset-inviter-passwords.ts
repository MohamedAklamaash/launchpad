import { initModels } from '@/db';
import { sequelize, closeDatabase } from '@/db/sequalize';
import { resetInviterChosenPasswords } from '@/service/inviter-password-reset.service';
import { logger } from '@/utils/logger';

// One-off operator script, never run automatically. Boot the new auth-service first (it adds
// the password_reset_required_at column), then:
//   pnpm reset:inviter-passwords -- --dry-run          (prints the count, writes nothing)
//   pnpm reset:inviter-passwords                       (resets every verified invited user)
//   pnpm reset:inviter-passwords -- --verified-before=2026-11-01T00:00:00Z
const readVerifiedBefore = (): Date | undefined => {
    const arg = process.argv.find((a) => a.startsWith('--verified-before='));
    if (!arg) return undefined;
    const date = new Date(arg.slice('--verified-before='.length));
    if (Number.isNaN(date.getTime())) throw new Error('--verified-before is not a valid date');
    return date;
};

const main = async () => {
    await sequelize.authenticate();
    await initModels();
    const result = await resetInviterChosenPasswords({
        dryRun: process.argv.includes('--dry-run'),
        verifiedBefore: readVerifiedBefore(),
    });
    logger.info({ result }, 'reset-inviter-passwords finished');
};

main()
    .then(() => closeDatabase())
    .catch(async (error: unknown) => {
        logger.error(error, 'reset-inviter-passwords failed');
        await closeDatabase();
        process.exitCode = 1;
    });
