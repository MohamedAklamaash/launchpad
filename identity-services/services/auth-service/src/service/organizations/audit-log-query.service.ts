import { Op, type WhereOptions } from 'sequelize';
import { Account, Membership, MembershipAuditEvent } from '@/db';
import type { AccessTokenPayload } from '@/utils/handle-token';
import type { OrgRole } from '@/types/org-role';
import { encodeAuditCursor, type AuditCursor, type AuditQuery } from '@/schemas/audit-event.schema';
import type { AuditAction } from './audit-log.service';
import { requireOrgManager } from './org-access.service';
import { canSeeEmails, displayNameFor } from './member-view';
import { enforceAuditReadSlot } from './org-rate-limit';

export interface AuditPerson {
    account_id: string;
    display_name: string;
    email?: string;
}

// No token, hash, invitation email or other free text: an invitation event carries only its
// action and role, and every person is an Account shown under the members-list rules.
export interface AuditEventView {
    id: string;
    action: AuditAction;
    occurred_at: Date;
    actor: AuditPerson | null;
    target: AuditPerson | null;
    old_role: string | null;
    new_role: string | null;
}

export interface AuditPage {
    events: AuditEventView[];
    next_cursor: string | null;
}

const beforeCursor = (cursor: AuditCursor): WhereOptions => ({
    [Op.or]: [
        { occurred_at: { [Op.lt]: cursor.occurredAt } },
        { occurred_at: cursor.occurredAt, id: { [Op.lt]: cursor.id } },
    ],
});

const buildWhere = (organizationId: string, query: AuditQuery): WhereOptions => {
    const clauses: WhereOptions[] = [{ organization_id: organizationId }];
    if (query.actor_account_id) clauses.push({ actor_account_id: query.actor_account_id });
    if (query.target_account_id) clauses.push({ target_account_id: query.target_account_id });
    if (query.action) clauses.push({ action: query.action });
    if (query.from) clauses.push({ occurred_at: { [Op.gte]: query.from } });
    if (query.to) clauses.push({ occurred_at: { [Op.lte]: query.to } });
    if (query.cursor) clauses.push(beforeCursor(query.cursor));
    return { [Op.and]: clauses };
};

const accountIdsIn = (rows: MembershipAuditEvent[]): string[] => {
    const ids = new Set<string>();
    rows.forEach((row) => {
        if (row.actor_account_id) ids.add(row.actor_account_id);
        if (row.target_account_id) ids.add(row.target_account_id);
    });
    return [...ids];
};

// Account rows outlive membership, so an address is shown only for someone who is still an
// active member of this org. A former member appears by id and sanitized name alone.
const activeMemberIds = async (organizationId: string, ids: string[]): Promise<Set<string>> => {
    const active = await Membership.findAll({
        where: { organization_id: organizationId, account_id: { [Op.in]: ids }, removed_at: null },
        attributes: ['account_id'],
    });
    return new Set(active.map((membership) => membership.account_id));
};

const loadPeople = async (
    organizationId: string,
    rows: MembershipAuditEvent[],
    viewerRole: OrgRole,
): Promise<Map<string, AuditPerson>> => {
    const ids = accountIdsIn(rows);
    if (ids.length === 0) return new Map();
    const active = await activeMemberIds(organizationId, ids);

    const accounts = await Account.findAll({
        where: { id: { [Op.in]: ids } },
        attributes: ['id', 'email', 'display_name'],
    });
    const withEmail = canSeeEmails(viewerRole);
    return new Map(
        accounts.map((account) => [
            account.id,
            {
                account_id: account.id,
                display_name: displayNameFor(account.display_name, viewerRole),
                ...(withEmail && active.has(account.id) ? { email: account.email } : {}),
            },
        ]),
    );
};

const personFor = (people: Map<string, AuditPerson>, accountId: string | null) => {
    if (!accountId) return null;
    return people.get(accountId) ?? { account_id: accountId, display_name: 'Member' };
};

// Owner and admin only (plan/F9 "Audit log"): the log names every actor and target in the
// org, which is manager-level information. A non-member, a member of another org and a
// missing org all get the 404 requireOrgManager produces. The path's organization id is
// the only scope; no filter can widen it.
export const listAuditEvents = async (
    caller: AccessTokenPayload,
    organizationId: string,
    query: AuditQuery,
): Promise<AuditPage> => {
    const manager = await requireOrgManager(caller, organizationId);
    await enforceAuditReadSlot(organizationId, manager.accountId);

    const rows = await MembershipAuditEvent.findAll({
        where: buildWhere(organizationId, query),
        order: [
            ['occurred_at', 'DESC'],
            ['id', 'DESC'],
        ],
        limit: query.limit + 1,
    });
    const page = rows.slice(0, query.limit);
    const people = await loadPeople(organizationId, page, manager.role);
    const hasMore = rows.length > query.limit;
    const last = page[page.length - 1];

    return {
        events: page.map((row) => ({
            id: row.id,
            action: row.action as AuditAction,
            occurred_at: row.occurred_at,
            actor: personFor(people, row.actor_account_id),
            target: personFor(people, row.target_account_id),
            old_role: row.old_role,
            new_role: row.new_role,
        })),
        next_cursor: hasMore && last ? encodeAuditCursor(last.occurred_at, last.id) : null,
    };
};
