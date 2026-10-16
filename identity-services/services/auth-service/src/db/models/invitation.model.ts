import { sequelize } from '@/db/sequalize';
import { DataTypes, Model, type Optional } from 'sequelize';
import { v7 as uuidv7 } from 'uuid';
import { ORG_ROLE_VALUES, type OrgRole } from '@/types/org-role';

export interface InvitationAttributes {
    id: string;
    organization_id: string;
    email: string;
    role: OrgRole;
    invited_by_account_id: string;
    // SHA-256 hex digest of the plaintext token, never the plaintext itself — direct copy
    // of Infrastructure.issue_onboarding_token's pattern (CLAUDE.md: "Onboarding token").
    // The plaintext is random (crypto.randomBytes), returned exactly once by
    // issueInvitation, and never derived from any id: an account id is a uuid5 of a
    // normalized email, so it (or any other id) must never double as a bearer credential.
    token_hash: string;
    expires_at: Date;
    accepted_at: Date | null;
    revoked_at: Date | null;
    created_at: Date;
}

export type InvitationCreationAttributes = Optional<
    InvitationAttributes,
    'id' | 'created_at' | 'accepted_at' | 'revoked_at'
>;

export class Invitation extends Model<InvitationAttributes, InvitationCreationAttributes> {
    declare id: string;
    declare organization_id: string;
    declare email: string;
    declare role: OrgRole;
    declare invited_by_account_id: string;
    declare token_hash: string;
    declare expires_at: Date;
    declare accepted_at: Date | null;
    declare revoked_at: Date | null;
    declare created_at: Date;
}

Invitation.init(
    {
        id: {
            type: DataTypes.UUID,
            defaultValue: () => uuidv7(),
            primaryKey: true,
        },
        organization_id: {
            type: DataTypes.UUID,
            allowNull: false,
        },
        email: {
            type: DataTypes.STRING,
            allowNull: false,
        },
        role: {
            type: DataTypes.ENUM(...ORG_ROLE_VALUES),
            allowNull: false,
        },
        invited_by_account_id: {
            type: DataTypes.UUID,
            allowNull: false,
        },
        token_hash: {
            type: DataTypes.STRING,
            allowNull: false,
            unique: true,
        },
        expires_at: {
            type: DataTypes.DATE,
            allowNull: false,
        },
        accepted_at: {
            type: DataTypes.DATE,
            allowNull: true,
        },
        revoked_at: {
            type: DataTypes.DATE,
            allowNull: true,
        },
        created_at: {
            type: DataTypes.DATE,
            allowNull: false,
            defaultValue: DataTypes.NOW,
        },
    },
    {
        sequelize,
        tableName: 'invitations',
        timestamps: false,
    },
);
