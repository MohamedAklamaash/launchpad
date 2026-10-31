import { z } from '@launchpad/common';
import { ORG_ROLE } from '@/types/org-role';

export const orgParamsSchema = z.object({
    id: z.uuid(),
});

export const orgInvitationParamsSchema = z.object({
    id: z.uuid(),
    invitationId: z.uuid(),
});

export const orgMemberParamsSchema = z.object({
    id: z.uuid(),
    accountId: z.uuid(),
});

// Strict on purpose: a body carrying an `id` (or any other key) is refused outright rather
// than silently stripped, so a client can never try to choose an organization id.
const ORG_NAME_INPUT_MAX = 200;
export const organizationNameSchema = z.strictObject({
    name: z.string().min(1).max(ORG_NAME_INPUT_MAX),
});

// `owner` is not a member of this enum: ownership moves only through transfer-ownership.
export const changeMemberRoleSchema = z.strictObject({
    role: z.enum([ORG_ROLE.ADMIN, ORG_ROLE.DEVELOPER, ORG_ROLE.VIEWER]),
});

export const transferOwnershipSchema = z.object({
    new_owner_account_id: z.uuid(),
});

export const createInvitationSchema = z.object({
    email: z.email().max(254),
    role: z.enum([ORG_ROLE.ADMIN, ORG_ROLE.DEVELOPER, ORG_ROLE.VIEWER]),
});

// 32 random bytes, base64url, unpadded: exactly 43 characters. Rejecting anything else at
// the edge keeps junk out of the hashing and lookup path.
export const acceptInvitationSchema = z.object({
    token: z.string().regex(/^[A-Za-z0-9_-]{43}$/),
});
