import { z } from '@launchpad/common';
import { ORG_ROLE } from '@/types/org-role';

export const orgParamsSchema = z.object({
    id: z.uuid(),
});

export const orgInvitationParamsSchema = z.object({
    id: z.uuid(),
    invitationId: z.uuid(),
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
