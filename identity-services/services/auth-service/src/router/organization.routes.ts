import { Router } from 'express';
import { validateRequest } from '@launchpad/common';
import {
    AcceptInvitation,
    CreateInvitation,
    ListInvitations,
    RevokeInvitation,
} from '@/controllers/organization.controller';
import { requireOrgsEnabled } from '@/middleware/orgs-enabled.middleware';
import {
    acceptInvitationSchema,
    createInvitationSchema,
    orgInvitationParamsSchema,
    orgParamsSchema,
} from '@/schemas/organization.schema';

export const organizationRouter: Router = Router();
export const invitationRouter: Router = Router();

organizationRouter.use(requireOrgsEnabled);
invitationRouter.use(requireOrgsEnabled);

/**
 * @swagger
 * /api/v1/organizations/{id}/invitations:
 *   post:
 *     summary: Invite an email address to an organization
 *     description: >
 *       Behind ORGS_ENABLED. Caller must be an active owner or admin of the organization
 *       (resolved from the token's account, never the request). Role must be admin,
 *       developer or viewer. Rate limited per organization and per target email; refused
 *       with 503 if the limiter is unavailable. The invitation token is only ever sent by
 *       email and is never returned.
 *     tags: [Organizations]
 *     security: [{ bearerAuth: [] }]
 *     parameters:
 *       - { in: path, name: id, required: true, schema: { type: string, format: uuid } }
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required: [email, role]
 *             properties:
 *               email: { type: string, format: email }
 *               role: { type: string, enum: [admin, developer, viewer] }
 *     responses:
 *       201: { description: Invitation created and email queued }
 *       403: { description: Caller is a member but not an owner or admin }
 *       404: { description: Organization not found, or the caller is not an active member }
 *       429: { description: Per-organization or per-email invite limit reached }
 *       503: { description: Rate limiter unavailable }
 *   get:
 *     summary: List the organization's pending invitations
 *     tags: [Organizations]
 *     security: [{ bearerAuth: [] }]
 *     parameters:
 *       - { in: path, name: id, required: true, schema: { type: string, format: uuid } }
 *     responses:
 *       200: { description: Pending (unaccepted, unrevoked, unexpired) invitations }
 *       403: { description: Caller is a member but not an owner or admin }
 *       404: { description: Organization not found, or the caller is not an active member }
 */
organizationRouter.post(
    '/:id/invitations',
    validateRequest({ params: orgParamsSchema, body: createInvitationSchema }),
    CreateInvitation,
);
organizationRouter.get(
    '/:id/invitations',
    validateRequest({ params: orgParamsSchema }),
    ListInvitations,
);

/**
 * @swagger
 * /api/v1/organizations/{id}/invitations/{invitationId}:
 *   delete:
 *     summary: Revoke a pending invitation
 *     tags: [Organizations]
 *     security: [{ bearerAuth: [] }]
 *     parameters:
 *       - { in: path, name: id, required: true, schema: { type: string, format: uuid } }
 *       - { in: path, name: invitationId, required: true, schema: { type: string, format: uuid } }
 *     responses:
 *       204: { description: Revoked }
 *       403: { description: Caller is a member but not an owner or admin }
 *       404: { description: Not found, already used, or belongs to another organization }
 */
organizationRouter.delete(
    '/:id/invitations/:invitationId',
    validateRequest({ params: orgInvitationParamsSchema }),
    RevokeInvitation,
);

/**
 * @swagger
 * /api/v1/invitations/accept:
 *   post:
 *     summary: Accept an organization invitation
 *     description: >
 *       Behind ORGS_ENABLED. Requires a session bearer token (Authorization header, so it
 *       is not CSRF-able) AND the invitation token in the body (never the URL). The
 *       session's credential must be email-verified and its email must equal the invited
 *       email. Every rejection reason returns the same 400.
 *     tags: [Organizations]
 *     security: [{ bearerAuth: [] }]
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required: [token]
 *             properties:
 *               token: { type: string }
 *     responses:
 *       200: { description: Membership created or revived }
 *       400: { description: Invitation invalid, expired, used, revoked, or for another account }
 *       409: { description: Already a member, or the account could not be linked }
 */
invitationRouter.post(
    '/accept',
    validateRequest({ body: acceptInvitationSchema }),
    AcceptInvitation,
);
