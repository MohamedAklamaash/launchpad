import { sequelize } from '@/db/sequalize';

import { UserOTP } from './models/invited-user-otp.model';
import { PasswordSettings } from './models/password-settings.model';
import { RefreshToken } from './models/refresh-token.model';
import { InvitedUser } from './models/invited-user.model';
import { User } from './models/user.model';
import { Account } from './models/account.model';
import { Organization } from './models/organization.model';
import { Membership } from './models/membership.model';
import { Invitation } from './models/invitation.model';
import { MembershipAuditEvent } from './models/membership-audit-event.model';
import { OrgEventOutbox } from './models/org-event-outbox.model';

// F9 ticket 2: exactly one `owner` membership per org, enforced by the database, not just
// application logic (plan/F9-teams-and-orgs.md, Decision: Data model). Declared here, not
// in Membership's own `indexes` option, so there is one source of truth for the exact SQL —
// both what runs on boot and what the no-Postgres unit test asserts against.
//
// F9 ticket 4: also excludes a tombstoned (removed_at IS NOT NULL) row — the owner can
// never actually be removed (removeMember refuses it), but a defensive exclusion here
// means a tombstoned owner row, if one ever existed, could never block a legitimate new
// owner at this organization_id. Named differently from the ticket-2 index below: `CREATE
// UNIQUE INDEX IF NOT EXISTS` is a no-op against an index that already exists under a
// given name, even with a different WHERE clause, so redefining it under the OLD name
// would never pick up this change on an environment where ticket 2's index already ran.
export const MEMBERSHIPS_ONE_OWNER_PER_ORG_LEGACY_INDEX_NAME = 'memberships_one_owner_per_org';
export const MEMBERSHIPS_ONE_OWNER_PER_ORG_INDEX_SQL =
    "CREATE UNIQUE INDEX IF NOT EXISTS memberships_one_owner_per_org_active ON memberships (organization_id) WHERE role = 'owner' AND removed_at IS NULL";

export const ORG_EVENT_OUTBOX_PENDING_INDEX_SQL =
    'CREATE INDEX IF NOT EXISTS org_event_outbox_pending ON org_event_outbox (seq) WHERE sent_at IS NULL';

// sequelize.sync() below creates missing tables but never alters existing ones (see
// memory/auth-service-no-migrations.md — this service has no migration framework), so a
// new column on an already-created table is silently ignored unless applied here by
// hand. IF NOT EXISTS makes this safe to run on every boot: a no-op once the column is
// there, and the one thing that actually adds it on an existing `invited_user_otp` table
// the first time this version deploys.
const applyPendingSchemaPatches = async () => {
    await sequelize.query(
        "ALTER TABLE invited_user_otp ADD COLUMN IF NOT EXISTS purpose VARCHAR(255) NOT NULL DEFAULT 'register'",
    );
    // H7: the OTP guess-attempt cap moved off Redis (which failed closed on an outage)
    // onto this account row, claimed with one atomic UPDATE ... RETURNING in
    // otp-attempts.ts. Every existing row defaults to attempts=0/no window, which is
    // exactly the state a never-attempted account should be in.
    await sequelize.query(
        'ALTER TABLE invited_users ADD COLUMN IF NOT EXISTS failed_otp_attempts INTEGER NOT NULL DEFAULT 0',
    );
    await sequelize.query(
        'ALTER TABLE invited_users ADD COLUMN IF NOT EXISTS otp_attempts_window_start TIMESTAMPTZ',
    );
    // F9 ticket 1: additive link to the new `accounts` table, nullable until backfilled
    // (see src/scripts/backfill-accounts.ts). `accounts` itself is a brand-new table, so
    // sequelize.sync() above creates it with no patch needed.
    await sequelize.query('ALTER TABLE users ADD COLUMN IF NOT EXISTS account_id UUID');
    await sequelize.query('ALTER TABLE invited_users ADD COLUMN IF NOT EXISTS account_id UUID');
    // `memberships` is itself a brand-new table so sequelize.sync() above creates its
    // plain unique index (organization_id, account_id) with no patch needed; only the
    // partial owner index needs this hand-applied step.
    //
    // F9 ticket 4: removeMember now tombstones instead of deleting, so the row needs a
    // removed_at the ticket-2 schema never had.
    await sequelize.query(
        'ALTER TABLE memberships ADD COLUMN IF NOT EXISTS removed_at TIMESTAMPTZ',
    );
    await sequelize.query(
        `DROP INDEX IF EXISTS ${MEMBERSHIPS_ONE_OWNER_PER_ORG_LEGACY_INDEX_NAME}`,
    );
    await sequelize.query(MEMBERSHIPS_ONE_OWNER_PER_ORG_INDEX_SQL);
    // F9 ticket 5: switch-org updates this column; /auth/refresh reads it so a rotated
    // access token carries the org the caller last switched to, not blindly back to their
    // personal org (plan/F9-teams-and-orgs.md, Decision: JWT and authorization).
    await sequelize.query('ALTER TABLE refresh_tokens ADD COLUMN IF NOT EXISTS active_org_id UUID');
    // Security review R1: distinct from is_authenticated, which register() resets to
    // false on every subsequent infra invite — see InvitedUserAttributes.email_verified_at.
    await sequelize.query(
        'ALTER TABLE invited_users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ',
    );
    await sequelize.query(
        'ALTER TABLE invited_users ADD COLUMN IF NOT EXISTS password_reset_required_at TIMESTAMPTZ',
    );
    // One-time backfill from the best signal available at the moment this column is
    // added: a row currently is_authenticated=true has, by definition, completed OTP
    // verification at least once. WHERE email_verified_at IS NULL makes this an
    // idempotent no-op on every boot after the first. A row that happens to be
    // is_authenticated=false right at this exact moment (e.g. a pending second-infra
    // invite reset it) is NOT backfilled here even if it was verified in the past —
    // the "unlink stale pre-fix links" step in backfillAccounts (account-backfill
    // .service.ts) is what reconciles any account_id such a row was left holding from
    // before this column existed.
    await sequelize.query(
        'UPDATE invited_users SET email_verified_at = NOW() WHERE is_authenticated = true AND email_verified_at IS NULL',
    );
    // `org_event_outbox` is a brand-new table (created by sync()); only the pending-row
    // index the dispatcher scans needs this hand-applied step.
    await sequelize.query(ORG_EVENT_OUTBOX_PENDING_INDEX_SQL);
};

export const initModels = async () => {
    await sequelize.sync();
    await applyPendingSchemaPatches();
};

export {
    UserOTP,
    PasswordSettings,
    RefreshToken,
    InvitedUser,
    User,
    Account,
    Organization,
    Membership,
    Invitation,
    MembershipAuditEvent,
    OrgEventOutbox,
};
