import type { Request, Response } from 'express';
import { HttpError } from '@launchpad/common';
import { getAuthHeader } from '@/utils/auth-header';
import { verifySessionToken } from '@/utils/handle-token';
import { canonicalizeUuid } from '@/utils/org-id';
import type { OrgRole } from '@/types/org-role';
import {
    createInvitation,
    listPendingInvitations,
    revokeOrgInvitation,
} from '@/service/organizations/invitation-admin.service';
import { transferOwnership } from '@/service/organizations/ownership-transfer.service';
import { acceptInvitation } from '@/service/organizations/invitation-accept.service';

const toHttpError = (error: unknown): HttpError =>
    error instanceof HttpError ? error : new HttpError(500, 'Internal Server Error');

const sessionFrom = (req: Request) => verifySessionToken(getAuthHeader(req));

export const CreateInvitation = async (req: Request, res: Response) => {
    try {
        const caller = sessionFrom(req);
        const { email, role } = req.body as { email: string; role: OrgRole };
        const invitation = await createInvitation({
            caller,
            organizationId: canonicalizeUuid(req.params.id as string),
            email,
            role,
        });
        return res.status(201).json(invitation);
    } catch (error: unknown) {
        throw toHttpError(error);
    }
};

export const ListInvitations = async (req: Request, res: Response) => {
    try {
        const caller = sessionFrom(req);
        const invitations = await listPendingInvitations(
            caller,
            canonicalizeUuid(req.params.id as string),
        );
        return res.status(200).json(invitations);
    } catch (error: unknown) {
        throw toHttpError(error);
    }
};

export const RevokeInvitation = async (req: Request, res: Response) => {
    try {
        const caller = sessionFrom(req);
        await revokeOrgInvitation(
            caller,
            canonicalizeUuid(req.params.id as string),
            canonicalizeUuid(req.params.invitationId as string),
        );
        return res.status(204).send();
    } catch (error: unknown) {
        throw toHttpError(error);
    }
};

export const AcceptInvitation = async (req: Request, res: Response) => {
    try {
        const caller = sessionFrom(req);
        const { token } = req.body as { token: string };
        const result = await acceptInvitation({ callerId: caller.sub, token });
        return res.status(200).json(result);
    } catch (error: unknown) {
        throw toHttpError(error);
    }
};

export const TransferOwnership = async (req: Request, res: Response) => {
    try {
        const caller = sessionFrom(req);
        const { new_owner_account_id } = req.body as { new_owner_account_id: string };
        const result = await transferOwnership({
            caller,
            organizationId: canonicalizeUuid(req.params.id as string),
            newOwnerAccountId: canonicalizeUuid(new_owner_account_id),
        });
        return res.status(200).json(result);
    } catch (error: unknown) {
        throw toHttpError(error);
    }
};
