import { sanitizeDisplayName } from '@launchpad/common';
import { Transaction } from 'sequelize';
import { Account, InvitedUser, User } from '@/db';
import { sequelize } from '@/db/sequalize';
import { accountIdForEmail, normalizeEmail } from '@/utils/account-id';
import { logger } from '@/utils/logger';

export type CredentialTable = 'users' | 'invited_users';

export interface LinkAccountInput {
    email: string;
    displayName: string;
    // Which table + row this credential belongs to — used both to perform the link and
    // to exclude this exact row from the "is this account already someone else's"
    // collision check below.
    table: CredentialTable;
    rowId: string;
    transaction: Transaction;
}

// Security review R2 (third bullet) + the "organic GitHub signups" follow-up: links a
// just-verified credential row (an InvitedUser that completed OTP verification, or a
// GitHub User on signup/login) to its deterministic Account — but ONLY when doing so
// would not silently merge two DIFFERENT credential rows into one Account on a live
// request path.
//
// That merge (two already-verified rows sharing a normalized email) is the backfill
// script's own explicit, reviewed, logged decision (F9 ticket 1 Non-goals) — a human
// looking at `backfillAccounts`'s collision log before it runs, not something a request
// handler gets to decide silently on every login/verification. If this account id is
// already linked to a different row than the one asking, this declines the link
// entirely (leaving `account_id` null, same as "never linked at all") and logs it to
// the same collision-review channel the backfill uses, rather than merging.
//
// Returns the account id now linked for this row, or `null` if it declined — the caller
// must treat `null` exactly like "no account_id", never retry-merge on its own.
export const linkAccountForVerifiedIdentity = async (
    input: LinkAccountInput,
): Promise<string | null> => {
    const accountId = accountIdForEmail(input.email);

    // Serializes concurrent links for the same Account: without it, a User login and an
    // InvitedUser verify can both pass the collision check below before either writes,
    // and both link. A blanket unique(account_id) per table cannot express the rule
    // because one Account may legitimately hold one User row and one InvitedUser row
    // after manual review; this lock makes check-then-write atomic without forbidding that.
    // Released automatically when the transaction ends.
    await sequelize.query('SELECT pg_advisory_xact_lock(hashtext(:key))', {
        replacements: { key: `account-link:${accountId}` },
        transaction: input.transaction,
    });

    const [existingUser, existingInvitedUser] = await Promise.all([
        User.findOne({ where: { account_id: accountId }, transaction: input.transaction }),
        InvitedUser.findOne({ where: { account_id: accountId }, transaction: input.transaction }),
    ]);

    const isThisRow = (table: CredentialTable, rowId: string) =>
        table === input.table && rowId === input.rowId;

    const conflictsWithAnotherRow =
        (existingUser && !isThisRow('users', existingUser.id)) ||
        (existingInvitedUser && !isThisRow('invited_users', existingInvitedUser.id));

    if (conflictsWithAnotherRow) {
        logger.warn(
            {
                accountBackfillCollision: {
                    email: normalizeEmail(input.email),
                    accountId,
                    blockedTable: input.table,
                    blockedRowId: input.rowId,
                },
            },
            'Declined to auto-link a credential row to an Account already linked to a different row at verify time — needs manual review',
        );
        return null;
    }

    await Account.findOrCreate({
        where: { id: accountId },
        defaults: {
            id: accountId,
            email: normalizeEmail(input.email),
            display_name: sanitizeDisplayName(input.displayName) || null,
        },
        transaction: input.transaction,
    });

    if (input.table === 'users') {
        await User.update(
            { account_id: accountId },
            { where: { id: input.rowId, account_id: null }, transaction: input.transaction },
        );
    } else {
        await InvitedUser.update(
            { account_id: accountId },
            { where: { id: input.rowId, account_id: null }, transaction: input.transaction },
        );
    }

    return accountId;
};
