import { randomBytes } from 'node:crypto';
import { Op, type WhereOptions } from 'sequelize';
import { InvitedUser, RefreshToken } from '@/db';
import { sequelize } from '@/db/sequalize';
import { hashPassword } from '@/utils/handle-password';
import { logger } from '@/utils/logger';

export interface InviterPasswordResetOptions {
    dryRun: boolean;
    // Only rows verified before this instant. Rows verified after the verify-time fix
    // shipped already hold a password the invitee chose (or an unusable one).
    verifiedBefore?: Date;
}

export interface InviterPasswordResetResult {
    dryRun: boolean;
    candidates: number;
    reset: number;
    sessionsRevoked: number;
}

const candidateFilter = (verifiedBefore?: Date): WhereOptions => ({
    email_verified_at: verifiedBefore
        ? { [Op.ne]: null, [Op.lt]: verifiedBefore }
        : { [Op.ne]: null },
    password_reset_required_at: null,
});

// There is no record of whether a verified invitee ever changed the password the inviter
// chose, so every verified invited row not already marked is reset. Marked rows are
// skipped, which makes a re-run a no-op. Existing refresh tokens are revoked: an inviter
// who logged in as the row holds a live session otherwise.
export const resetInviterChosenPasswords = async (
    options: InviterPasswordResetOptions,
): Promise<InviterPasswordResetResult> => {
    const where = candidateFilter(options.verifiedBefore);

    if (options.dryRun) {
        const candidates = await InvitedUser.count({ where });
        logger.info({ candidates }, 'Dry run: invited users that would need a password reset');
        return { dryRun: true, candidates, reset: 0, sessionsRevoked: 0 };
    }

    const unusableHash = await hashPassword(randomBytes(32).toString('hex'));
    const result = await sequelize.transaction(async (transaction) => {
        const rows = await InvitedUser.findAll({ where, attributes: ['id'], transaction });
        const ids = rows.map((row) => row.id);
        if (ids.length === 0) return { candidates: 0, reset: 0, sessionsRevoked: 0 };

        const [reset] = await InvitedUser.update(
            { password_hash: unusableHash, password_reset_required_at: new Date() },
            { where: { id: { [Op.in]: ids } }, transaction },
        );
        const sessionsRevoked = await RefreshToken.destroy({
            where: { user_id: { [Op.in]: ids } },
            transaction,
        });
        return { candidates: ids.length, reset, sessionsRevoked };
    });

    logger.info({ inviterPasswordReset: result }, 'Inviter-chosen passwords reset');
    return { dryRun: false, ...result };
};
