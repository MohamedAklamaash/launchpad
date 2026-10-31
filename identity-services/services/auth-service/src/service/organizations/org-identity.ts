import { Op, type Transaction } from 'sequelize';
import { InvitedUser, User } from '@/db';
import { personalOrgId } from '@/utils/org-id';

// An email-verified credential is the only proof the Account belongs to a real person who
// controls that address. A GitHub `User` row has no email_verified_at column (OAuth is the
// verification), an `InvitedUser` row must carry one. An Account held by more than one
// credential row is refused: any holder could act as the Account, and an inviter may have
// chosen one holder's password (the same rule that keeps shared Accounts out of personal orgs).
export const hasVerifiedCredential = async (
    accountId: string,
    transaction?: Transaction,
): Promise<boolean> => {
    const [users, invitedUsers] = await Promise.all([
        User.findAll({ where: { account_id: accountId }, attributes: ['id'], transaction }),
        InvitedUser.findAll({
            where: { account_id: accountId },
            attributes: ['id', 'email_verified_at'],
            transaction,
        }),
    ]);
    const holders = [...users, ...invitedUsers];
    if (holders.length !== 1) return false;
    return users.length === 1 || invitedUsers[0]!.email_verified_at != null;
};

// A personal org (id = uuid5(users.id)) is the legacy owner's home: Django stamps their
// pre-org infrastructures into it, the id is derivable from the user id alone, and every
// GitHub login re-runs ensurePersonalOrganization, which reports `conflict` forever once
// the owner differs. It is therefore never transferable, deletable or leavable by its owner.
// Judged from the org's owner, so a member invited into someone else's personal org sees it
// as personal too.
export interface OrgOwnerRef {
    id: string;
    owner_account_id: string;
}

export const personalOrgIdsAmong = async (
    orgs: OrgOwnerRef[],
    transaction?: Transaction,
): Promise<Set<string>> => {
    if (orgs.length === 0) return new Set();
    const users = await User.findAll({
        where: { account_id: { [Op.in]: [...new Set(orgs.map((org) => org.owner_account_id))] } },
        attributes: ['id', 'account_id'],
        transaction,
    });
    const derived = new Set(users.map((user) => `${user.account_id}:${personalOrgId(user.id)}`));
    return new Set(
        orgs.filter((org) => derived.has(`${org.owner_account_id}:${org.id}`)).map((org) => org.id),
    );
};

export const isPersonalOrganization = async (
    organizationId: string,
    ownerAccountId: string,
    transaction?: Transaction,
): Promise<boolean> =>
    (
        await personalOrgIdsAmong(
            [{ id: organizationId, owner_account_id: ownerAccountId }],
            transaction,
        )
    ).has(organizationId);
