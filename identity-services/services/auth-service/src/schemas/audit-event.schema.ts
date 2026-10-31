import { z } from '@launchpad/common';
import { AUDIT_ACTIONS } from '@/service/organizations/audit-log.service';

export const AUDIT_PAGE_DEFAULT = 50;
export const AUDIT_PAGE_MAX = 100;

export interface AuditCursor {
    occurredAt: Date;
    id: string;
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const CURSOR_PATTERN = /^[A-Za-z0-9_-]{1,100}$/;

// The cursor is a position, not a credential: it only says "rows older than (time, id)".
// The organization filter is applied on every page regardless, so a forged cursor can move
// within the caller's own org and nowhere else.
export const encodeAuditCursor = (occurredAt: Date, id: string): string =>
    Buffer.from(`${occurredAt.getTime()}.${id}`).toString('base64url');

export const decodeAuditCursor = (raw: string): AuditCursor | null => {
    if (!CURSOR_PATTERN.test(raw)) return null;
    const [millis, id, extra] = Buffer.from(raw, 'base64url').toString('utf8').split('.');
    if (extra !== undefined || !millis || !id) return null;
    if (!/^\d{1,15}$/.test(millis) || !UUID_PATTERN.test(id)) return null;
    return { occurredAt: new Date(Number(millis)), id };
};

const accountFilter = z.uuid().transform((value) => value.toLowerCase());

// Strict on purpose: an unknown query key is a client bug, and refusing it keeps the
// documented filter set the only one.
export const auditQuerySchema = z
    .strictObject({
        limit: z.coerce.number().int().min(1).max(AUDIT_PAGE_MAX).default(AUDIT_PAGE_DEFAULT),
        cursor: z
            .string()
            .refine((value) => decodeAuditCursor(value) !== null, 'Invalid cursor')
            .transform((value) => decodeAuditCursor(value) as AuditCursor)
            .optional(),
        actor_account_id: accountFilter.optional(),
        target_account_id: accountFilter.optional(),
        action: z.enum(AUDIT_ACTIONS).optional(),
        from: z.iso
            .datetime()
            .transform((value) => new Date(value))
            .optional(),
        to: z.iso
            .datetime()
            .transform((value) => new Date(value))
            .optional(),
    })
    .refine((query) => !query.from || !query.to || query.from <= query.to, {
        message: '`from` must not be after `to`',
        path: ['from'],
    });

export type AuditQuery = z.output<typeof auditQuerySchema>;
