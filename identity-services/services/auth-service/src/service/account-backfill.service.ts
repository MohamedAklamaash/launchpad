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
    // Security review H1(c): InvitedUser rows left unlinked this pass because they
    // haven't completed OTP verification yet — never zero in a system where invites
    // are pending, and NOT an error. Logged per-group at WARN (below) so an email that
    // collides with an already-linked (verified) identity is visible for manual review
    // rather than silently merged.
    unverifiedRowsSkipped: number;
}

interface CredentialRow {
    id: string;
    email: string;
    user_name: string;
    // Security review H1(c): absent (User rows — GitHub OAuth is itself the proof of
    // email ownership, so every User row is implicitly verified) or the InvitedUser
    // row's real value. An inviter can create an InvitedUser row with any email and a
    // password of their own choosing; that row must never be linked to an Account (and
    // therefore never able to inherit another identity's org memberships via a shared
    // email) until it proves ownership by completing OTP verification.
    is_authenticated?: boolean;
}

interface EmailGroup {
    accountId: string;
    displayName: string;
    userIds: string[];
    invitedUserIds: string[];
}

const isLinkable = (row: CredentialRow): boolean => row.is_authenticated !== false;

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

    // Only verified rows are grouped for linking — an unverified InvitedUser never
    // enters this map, so it can never be bulk-created into (or linked onto) an Account
    // another, already-verified identity owns.
    for (const user of users.filter(isLinkable)) addRow(user, 'userIds');
    for (const invitedUser of invitedUsers.filter(isLinkable))
        addRow(invitedUser, 'invitedUserIds');

    return groups;
};

// Every normalized email backed by more than one credential row across the FULL set
// (verified and unverified together) — logged for manual review regardless of whether
// this pass actually links anything for that email. This is what makes an
// attacker-planted unverified InvitedUser sharing a real user's email visible to ops,
// not just silently inert.
const logCollisionsForReview = (
    allRows: (CredentialRow & { table: 'users' | 'invited_users' })[],
) => {
    const byEmail = new Map<string, (CredentialRow & { table: 'users' | 'invited_users' })[]>();
    for (const row of allRows) {
        const normalized = normalizeEmail(row.email);
        const existing = byEmail.get(normalized);
        if (existing) existing.push(row);
        else byEmail.set(normalized, [row]);
    }

    let collisionGroups = 0;
    for (const [email, rows] of byEmail) {
        if (rows.length < 2) continue;
        collisionGroups += 1;
        logger.warn(
            {
                accountBackfillCollision: {
                    email,
                    rows: rows.map((r) => ({
                        id: r.id,
                        table: r.table,
                        is_authenticated: r.is_authenticated ?? true,
                    })),
                },
            },
            "Account backfill: multiple credential rows share a normalized email — review for an unverified row riding a verified identity's email before trusting any merge",
        );
    }
    return collisionGroups;
};

// Computes uuid5(ACCOUNT_NAMESPACE, normalized email) for every VERIFIED existing
// users/invited_users row, creates one Account per distinct normalized email (deduped,
// per F9 ticket 1's Execution plan — see plan/F9-teams-and-orgs.md), and links each
// verified credential row's account_id. Safe to run any number of times: Account ids
// are deterministic so re-creating one is a no-op, and every link update is guarded by
// `account_id IS NULL` so a row already linked (by this script, by the verify-time hook
// in InvitedUserAuthService.authenticateWithOTP, or by a later organic uuidv7 Account
// from a post-F9-ticket-1 signup) is never overwritten.
//
// Security review H1(c): an unverified InvitedUser row is never linked here, no matter
// what email it carries or how many other rows already share that email. It becomes
// eligible only once it is itself verified (authenticateWithOTP links it directly, at
// the moment of verification — see that function) or a later backfill pass runs after
// it was verified through some other path.
export const backfillAccounts = async (): Promise<AccountBackfillResult> => {
    const [users, invitedUsers] = await Promise.all([
        User.findAll({ attributes: ['id', 'email', 'user_name'] }),
        InvitedUser.findAll({ attributes: ['id', 'email', 'user_name', 'is_authenticated'] }),
    ]);

    const userRows = users.map((u) => u.get({ plain: true }) as CredentialRow);
    const invitedUserRows = invitedUsers.map((u) => u.get({ plain: true }) as CredentialRow);

    const collisionGroups = logCollisionsForReview([
        ...userRows.map((r) => ({ ...r, table: 'users' as const })),
        ...invitedUserRows.map((r) => ({ ...r, table: 'invited_users' as const })),
    ]);
    const unverifiedRowsSkipped = invitedUserRows.filter((r) => !isLinkable(r)).length;

    const groups = groupByNormalizedEmail(userRows, invitedUserRows);

    if (groups.size === 0) {
        return {
            accountsCreated: 0,
            usersLinked: 0,
            invitedUsersLinked: 0,
            collisionGroups,
            unverifiedRowsSkipped,
        };
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

    for (const group of groups.values()) {
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
        unverifiedRowsSkipped,
    };

    logger.info({ accountBackfill: result }, 'Account backfill pass complete');

    return result;
};
