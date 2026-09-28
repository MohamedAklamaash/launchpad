import { sequelize } from '@/db/sequalize';

import { UserOTP } from './models/invited-user-otp.model';
import { PasswordSettings } from './models/password-settings.model';
import { RefreshToken } from './models/refresh-token.model';
import { InvitedUser } from './models/invited-user.model';
import { User } from './models/user.model';

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
};

export const initModels = async () => {
    await sequelize.sync();
    await applyPendingSchemaPatches();
};

export { UserOTP, PasswordSettings, RefreshToken, InvitedUser, User };
