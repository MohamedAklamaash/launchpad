import { sanitizeDisplayName } from '@launchpad/common';
import { Op, type Transaction } from 'sequelize';
import { Account, InvitedUser, User } from '@/db';
import { sequelize } from '@/db/sequalize';
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
    // Security review R1 follow-up: rows that picked up an account_id under the OLD
    // is_authenticated-based linking rule, then had is_authenticated reset to false by
    // a later infra invite BEFORE this pass ever ran (so they were never backfilled an
    // email_verified_at either) — see "unlink stale pre-fix links" below. Nulled so the
    // row goes through authenticateWithOTP's verify hook again to get correctly
    // relinked with email_verified_at set.
    staleLinksUnlinked: number;
    // Verified rows left unlinked because linking them would put a second credential row
    // on an Account another row already holds (or two unlinked rows would both claim it).
    // The live link path declines the same merge; here it is logged for a human instead.
    sharedAccountRowsSkipped: number;
}

interface CredentialRow {
    id: string;
    email: string;
    user_name: string;
    // Security review H1(c), revised under R1: absent (User rows — GitHub OAuth is
    // itself the proof of email ownership, so every User row is implicitly verified)
    // or the InvitedUser row's real `email_verified_at`. Deliberately not
    // `is_authenticated`, which register() resets to `false` on every subsequent
    // infra invite — that would make an already-verified row look unlinkable again
    // the moment it's invited anywhere else. An inviter can create an InvitedUser row
    // with any email and a password of their own choosing; that row must never be
    // linked to an Account (and therefore never able to inherit another identity's org
    // memberships via a shared email) until it proves ownership by completing OTP
    // verification once, ever.
    email_verified_at?: Date | null;
    account_id?: string | null;
}

interface EmailGroup {
    accountId: string;
    displayName: string;
    userIds: string[];
    invitedUserIds: string[];
    // Rows (any table, verified or not) already holding this Account.
    holderCount: number;
}

const isLinkable = (row: CredentialRow): boolean => row.email_verified_at !== null;

const groupByNormalizedEmail = (
    users: CredentialRow[],
    invitedUsers: CredentialRow[],
): Map<string, EmailGroup> => {
    const groups = new Map<string, EmailGroup>();

    const groupFor = (row: CredentialRow): EmailGroup => {
        const normalized = normalizeEmail(row.email);
        let group = groups.get(normalized);
        if (!group) {
            group = {
                accountId: accountIdForEmail(row.email),
                displayName: row.user_name,
                userIds: [],
                invitedUserIds: [],
                holderCount: 0,
            };
            groups.set(normalized, group);
        }
        return group;
    };

    const addRow = (row: CredentialRow, target: 'userIds' | 'invitedUserIds') => {
        const group = groupFor(row);
        if (row.account_id === group.accountId) group.holderCount += 1;
        else if (!row.account_id) group[target].push(row.id);
    };

    // Only verified rows are grouped for linking — an unverified InvitedUser never
    // enters this map, so it can never be bulk-created into (or linked onto) an Account
    // another, already-verified identity owns.
    for (const user of users.filter(isLinkable)) addRow(user, 'userIds');
    for (const invitedUser of invitedUsers.filter(isLinkable))
        addRow(invitedUser, 'invitedUserIds');

    return groups;
};

// Live-path rule (account-link.service.ts): an Account is never extended to a second
// credential row. A group links its single unlinked verified row only when no other row
// holds the Account; with a holder already present, or with two or more unlinked rows
// competing, nothing is linked and the rows are logged for a human to resolve. Whoever
// holds an Account is reachable by anyone who can log in as ANY of its rows, so merging
// is a decision about one person controlling both, never a default.
const planLinks = (groups: Map<string, EmailGroup>) => {
    const linkable = new Map<string, EmailGroup>();
    let sharedAccountRowsSkipped = 0;

    for (const [email, group] of groups) {
        const unlinkedCount = group.userIds.length + group.invitedUserIds.length;
        if (unlinkedCount === 0) continue;
        if (group.holderCount > 0 || unlinkedCount > 1) {
            sharedAccountRowsSkipped += unlinkedCount;
            logger.warn(
                {
                    accountBackfillSharedAccount: {
                        email,
                        accountId: group.accountId,
                        existingHolders: group.holderCount,
                        skippedUsers: group.userIds,
                        skippedInvitedUsers: group.invitedUserIds,
                    },
                },
                'Account backfill: refusing to link a credential row into an Account another row holds or contests - confirm one person controls both before linking by hand',
            );
            continue;
        }
        linkable.set(email, group);
    }
    return { linkable, sharedAccountRowsSkipped };
};

interface GroupLinkOutcome {
    usersLinked: number;
    invitedUsersLinked: number;
    skipped: number;
}

const holdsAccount = async (accountId: string, transaction: Transaction): Promise<boolean> => {
    const [user, invitedUser] = await Promise.all([
        User.findOne({ where: { account_id: accountId }, transaction }),
        InvitedUser.findOne({ where: { account_id: accountId }, transaction }),
    ]);
    return user !== null || invitedUser !== null;
};

// Same advisory lock as linkAccountForVerifiedIdentity, so a live link and this pass cannot
// both pass their holder check before either writes. The holder check is repeated under the
// lock because the plan above was computed from a snapshot taken before it.
const linkGroupUnderLock = (group: EmailGroup): Promise<GroupLinkOutcome> =>
    sequelize.transaction(async (transaction) => {
        await sequelize.query('SELECT pg_advisory_xact_lock(hashtext(:key))', {
            replacements: { key: `account-link:${group.accountId}` },
            transaction,
        });

        const rowCount = group.userIds.length + group.invitedUserIds.length;
        if (await holdsAccount(group.accountId, transaction)) {
            logger.warn(
                { accountBackfillSharedAccount: { accountId: group.accountId, raced: true } },
                'Account backfill: Account was claimed by another row before the link - skipped',
            );
            return { usersLinked: 0, invitedUsersLinked: 0, skipped: rowCount };
        }

        let usersLinked = 0;
        let invitedUsersLinked = 0;
        if (group.userIds.length > 0) {
            [usersLinked] = await User.update(
                { account_id: group.accountId },
                { where: { id: { [Op.in]: group.userIds }, account_id: null }, transaction },
            );
        }
        if (group.invitedUserIds.length > 0) {
            [invitedUsersLinked] = await InvitedUser.update(
                { account_id: group.accountId },
                { where: { id: { [Op.in]: group.invitedUserIds }, account_id: null }, transaction },
            );
        }
        return { usersLinked, invitedUsersLinked, skipped: 0 };
    });

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
                        verified: r.email_verified_at !== null,
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
    // Security review R1 follow-up: a row linked under the old is_authenticated-based
    // rule, then reset to is_authenticated=false by a later infra invite before the
    // email_verified_at migration/backfill (db/index.ts) ever reached it, is left
    // holding an account_id this pass can no longer vouch for under the new, stricter
    // criterion. Run first, unconditionally, so such a row never gets counted as
    // "already linked" by the grouping logic below — it goes through
    // authenticateWithOTP's verify hook again to get correctly relinked.
    const [staleLinksUnlinked] = await InvitedUser.update(
        { account_id: null },
        { where: { email_verified_at: null, account_id: { [Op.ne]: null } } },
    );

    const [users, invitedUsers] = await Promise.all([
        User.findAll({ attributes: ['id', 'email', 'user_name', 'account_id'] }),
        InvitedUser.findAll({
            attributes: ['id', 'email', 'user_name', 'email_verified_at', 'account_id'],
        }),
    ]);

    const userRows = users.map((u) => u.get({ plain: true }) as CredentialRow);
    const invitedUserRows = invitedUsers.map((u) => u.get({ plain: true }) as CredentialRow);

    const collisionGroups = logCollisionsForReview([
        ...userRows.map((r) => ({ ...r, table: 'users' as const })),
        ...invitedUserRows.map((r) => ({ ...r, table: 'invited_users' as const })),
    ]);
    const unverifiedRowsSkipped = invitedUserRows.filter((r) => !isLinkable(r)).length;

    const { linkable: groups, sharedAccountRowsSkipped } = planLinks(
        groupByNormalizedEmail(userRows, invitedUserRows),
    );

    if (groups.size === 0) {
        return {
            accountsCreated: 0,
            usersLinked: 0,
            invitedUsersLinked: 0,
            collisionGroups,
            unverifiedRowsSkipped,
            staleLinksUnlinked,
            sharedAccountRowsSkipped,
        };
    }

    const accountRecords = Array.from(groups.entries()).map(([email, group]) => ({
        id: group.accountId,
        email,
        display_name: sanitizeDisplayName(group.displayName) || null,
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
    let racedSkipped = 0;

    for (const group of groups.values()) {
        const outcome = await linkGroupUnderLock(group);
        usersLinked += outcome.usersLinked;
        invitedUsersLinked += outcome.invitedUsersLinked;
        racedSkipped += outcome.skipped;
    }

    const result: AccountBackfillResult = {
        accountsCreated: newAccountRecords.length,
        usersLinked,
        invitedUsersLinked,
        collisionGroups,
        unverifiedRowsSkipped,
        staleLinksUnlinked,
        sharedAccountRowsSkipped: sharedAccountRowsSkipped + racedSkipped,
    };

    logger.info({ accountBackfill: result }, 'Account backfill pass complete');

    return result;
};
