import { Op } from 'sequelize';
import { Account, InvitedUser, User } from '@/db';
import { accountIdForEmail, normalizeEmail } from '@/utils/account-id';
import { logger } from '@/utils/logger';

export interface AccountBackfillResult {
    accountsCreated: number;
    usersLinked: number;
    invitedUsersLinked: number;
    // Distinct normalized emails backed by more than one credential row (same table or
    // across tables) — the email-collision-across-tables behavior F9's Non-goals flags as
    // documented, not fixed: those rows deliberately end up sharing one Account id.
    collisionGroups: number;
}

interface CredentialRow {
    id: string;
    email: string;
    user_name: string;
}

interface EmailGroup {
    accountId: string;
    displayName: string;
    userIds: string[];
    invitedUserIds: string[];
}

const groupByNormalizedEmail = (
    users: CredentialRow[],
    invitedUsers: CredentialRow[],
): Map<string, EmailGroup> => {
    const groups = new Map<string, EmailGroup>();

    const addRow = (row: CredentialRow, target: 'userIds' | 'invitedUserIds') => {
        const normalized = normalizeEmail(row.email);
        let group = groups.get(normalized);
        if (!group) {
            group = {
                accountId: accountIdForEmail(row.email),
                displayName: row.user_name,
                userIds: [],
                invitedUserIds: [],
            };
            groups.set(normalized, group);
        }
        group[target].push(row.id);
    };

    for (const user of users) addRow(user, 'userIds');
    for (const invitedUser of invitedUsers) addRow(invitedUser, 'invitedUserIds');

    return groups;
};

// Computes uuid5(ACCOUNT_NAMESPACE, normalized email) for every existing users/invited_users
// row, creates one Account per distinct normalized email (deduped, per F9 ticket 1's
// Execution plan — see plan/F9-teams-and-orgs.md), and links each credential row's
// account_id. Safe to run any number of times: Account ids are deterministic so re-creating
// one is a no-op, and every link update is guarded by `account_id IS NULL` so a row already
// linked (by this script, or later by an organic uuidv7 Account from a post-F9-ticket-1
// signup) is never overwritten.
export const backfillAccounts = async (): Promise<AccountBackfillResult> => {
    const [users, invitedUsers] = await Promise.all([
        User.findAll({ attributes: ['id', 'email', 'user_name'] }),
        InvitedUser.findAll({ attributes: ['id', 'email', 'user_name'] }),
    ]);

    const groups = groupByNormalizedEmail(
        users.map((u) => u.get({ plain: true }) as CredentialRow),
        invitedUsers.map((u) => u.get({ plain: true }) as CredentialRow),
    );

    if (groups.size === 0) {
        return { accountsCreated: 0, usersLinked: 0, invitedUsersLinked: 0, collisionGroups: 0 };
    }

    const accountRecords = Array.from(groups.entries()).map(([email, group]) => ({
        id: group.accountId,
        email,
        display_name: group.displayName,
    }));

    // Model.bulkCreate always resolves to one built instance per input record, even for
    // rows a concurrent/`ignoreDuplicates` conflict caused Postgres to skip inserting — its
    // return value does not report which rows were actually new. Querying existing ids
    // first is what makes accountsCreated below an honest count on a second (or Nth) run,
    // not just the group count every time. `ignoreDuplicates` stays on bulkCreate as the
    // actual safety net against a genuine race (another process inserting the same id
    // between this query and the insert) — it just stops being the count's source.
    const candidateIds = accountRecords.map((record) => record.id);
    const existingAccounts = await Account.findAll({
        where: { id: { [Op.in]: candidateIds } },
        attributes: ['id'],
    });
    const existingIds = new Set(existingAccounts.map((account) => account.id));
    const newAccountRecords = accountRecords.filter((record) => !existingIds.has(record.id));

    if (newAccountRecords.length > 0) {
        await Account.bulkCreate(newAccountRecords, { ignoreDuplicates: true });
    }

    let usersLinked = 0;
    let invitedUsersLinked = 0;
    let collisionGroups = 0;

    for (const group of groups.values()) {
        if (group.userIds.length + group.invitedUserIds.length > 1) {
            collisionGroups += 1;
        }

        if (group.userIds.length > 0) {
            const [linked] = await User.update(
                { account_id: group.accountId },
                { where: { id: { [Op.in]: group.userIds }, account_id: null } },
            );
            usersLinked += linked;
        }

        if (group.invitedUserIds.length > 0) {
            const [linked] = await InvitedUser.update(
                { account_id: group.accountId },
                { where: { id: { [Op.in]: group.invitedUserIds }, account_id: null } },
            );
            invitedUsersLinked += linked;
        }
    }

    const result: AccountBackfillResult = {
        accountsCreated: newAccountRecords.length,
        usersLinked,
        invitedUsersLinked,
        collisionGroups,
    };

    logger.info({ accountBackfill: result }, 'Account backfill pass complete');

    return result;
};
