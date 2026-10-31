import { sequelize } from '@/db/sequalize';
import { DataTypes, Model, type Optional } from 'sequelize';

export interface OrgEventOutboxAttributes {
    // Insertion order. Rows for the same membership are written under that membership's row
    // lock, so seq order is commit order wherever order matters.
    seq: string;
    organization_id: string;
    routing_key: string;
    payload: Record<string, unknown>;
    created_at: Date;
    sent_at: Date | null;
}

export type OrgEventOutboxCreationAttributes = Optional<
    OrgEventOutboxAttributes,
    'seq' | 'created_at' | 'sent_at'
>;

// Transactional outbox for the org.events exchange: every state change that publishes an
// event inserts one row here in the same transaction, and the dispatcher
// (org-event-outbox.service.ts) publishes pending rows in seq order on a confirm channel.
export class OrgEventOutbox extends Model<
    OrgEventOutboxAttributes,
    OrgEventOutboxCreationAttributes
> {
    declare seq: string;
    declare organization_id: string;
    declare routing_key: string;
    declare payload: Record<string, unknown>;
    declare created_at: Date;
    declare sent_at: Date | null;
}

OrgEventOutbox.init(
    {
        seq: { type: DataTypes.BIGINT, autoIncrement: true, primaryKey: true },
        organization_id: { type: DataTypes.UUID, allowNull: false },
        routing_key: { type: DataTypes.STRING, allowNull: false },
        payload: { type: DataTypes.JSONB, allowNull: false },
        created_at: { type: DataTypes.DATE, allowNull: false, defaultValue: DataTypes.NOW },
        sent_at: { type: DataTypes.DATE, allowNull: true, defaultValue: null },
    },
    {
        sequelize,
        tableName: 'org_event_outbox',
        timestamps: false,
    },
);
