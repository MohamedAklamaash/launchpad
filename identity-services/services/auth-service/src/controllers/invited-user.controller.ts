import { Request, Response } from 'express';
import { InvitedUserFacade } from '@/service/invited-user.facade.service';
import { HttpError } from '@launchpad/common';
import { USER_ROLE } from '@/types/auth.invited_user.types';
import { getAuthHeader } from '@/utils/auth-header';
import { verifyAccessToken } from '@/utils/handle-token';
import { superAdminMiddleware } from '@/utils/super-admin';
import { env } from '@/config/env';

const invitedUserFacade = new InvitedUserFacade();

export const RegisterInvitedUser = async (req: Request, res: Response) => {
    try {
        const token = getAuthHeader(req);
        const { email, password, user_name, infra_id, role } = req.body;
        const payload = verifyAccessToken(token);
        const super_user = await superAdminMiddleware(payload);
        if (!super_user.infra_id.includes(infra_id)) {
            throw new HttpError(
                401,
                `Unauthorized, user:${payload.user_name} is not authorized to invite users to ${infra_id}`,
            );
        }
        const { user, otp } = await invitedUserFacade.register(
            {
                email,
                password,
                user_name,
                infra_id,
                role: role as USER_ROLE,
            },
            super_user.id,
        );

        // Never serialize the raw model — it carries password_hash and OTP linkage.
        // The OTP is delivered by email; only echo it outside production for local/e2e flows.
        const body: Record<string, unknown> = {
            user: {
                id: user.id,
                email: user.email,
                user_name: user.user_name,
                role: user.role,
                infra_id: user.infra_id,
                invited_by: user.invited_by,
                created_at: user.created_at,
            },
        };
        if (env.NODE_ENV !== 'production') {
            body.otp = otp;
        }

        return res.status(201).json(body);
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, 'Internal Server Error');
    }
};

export const ListInvitedUsers = async (req: Request, res: Response) => {
    try {
        const token = getAuthHeader(req);
        const payload = verifyAccessToken(token);
        const invitees = await invitedUserFacade.listInvitedBy(payload.sub);
        return res.status(200).json(
            invitees.map((u) => ({
                id: u.id,
                email: u.email,
                user_name: u.user_name,
                role: u.role,
                infra_id: u.infra_id,
                is_authenticated: u.is_authenticated,
                created_at: u.created_at,
            })),
        );
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        console.error('ListInvitedUsers failed', error);
        throw new HttpError(500, 'Internal Server Error');
    }
};

export const RemoveMemberFromOrg = async (req: Request, res: Response) => {
    try {
        const token = getAuthHeader(req);
        const payload = verifyAccessToken(token);
        if (payload.role !== USER_ROLE.SUPER_ADMIN && payload.role !== USER_ROLE.ADMIN) {
            throw new HttpError(403, 'Only admins can remove members');
        }
        const { userId } = req.params as { userId: string };
        const { infra_ids } = (req.body ?? {}) as { infra_ids?: string[] };
        const result = await invitedUserFacade.removeFromOrg(
            payload.sub,
            payload.role,
            userId,
            infra_ids ?? [],
        );
        return res.status(200).json(result);
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        console.error('RemoveMemberFromOrg failed', error);
        throw new HttpError(500, 'Internal Server Error');
    }
};

export const LoginUser = async (req: Request, res: Response) => {
    try {
        const { email, password } = req.body;
        const authRes = await invitedUserFacade.login({ email, password });
        return res.status(200).json(authRes);
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, 'Internal Server Error');
    }
};

export const AuthenticateOTP = async (req: Request, res: Response) => {
    try {
        const { email, otp } = req.query as { email: string; otp: string };
        const authRes = await invitedUserFacade.authenticateWithOTP({
            email,
            otp,
        });

        return res.status(200).json(authRes);
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, 'Internal Server Error');
    }
};

export const ForgotPassword = async (req: Request, res: Response) => {
    try {
        const { email } = req.body;
        const otp = await invitedUserFacade.forgotPassword({ email });
        return res.status(200).json({ otp });
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, 'Internal Server Error');
    }
};

export const VerifyResetOTP = async (req: Request, res: Response) => {
    try {
        const { email, otp } = req.body;
        const success = await invitedUserFacade.verifyResetOTP({
            email,
            otp,
        });
        return res.status(200).json({ success });
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, 'Internal Server Error');
    }
};

export const ResetPassword = async (req: Request, res: Response) => {
    try {
        const { token, newPassword } = req.body;
        const success = await invitedUserFacade.resetPassword({
            reset_token: token,
            new_password: newPassword,
        });
        return res.status(200).json({ success });
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, 'Internal Server Error');
    }
};

export const UpdatePassword = async (req: Request, res: Response) => {
    try {
        const { email, oldPassword, newPassword } = req.body;
        const success = await invitedUserFacade.updatePassword({
            email,
            old_password: oldPassword,
            new_password: newPassword,
        });

        return res.status(200).json({ success });
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, 'Internal Server Error');
    }
};

export const RefreshTokenForUser = async (req: Request, res: Response) => {
    try {
        const { token } = req.body;
        const authRes = await invitedUserFacade.refresh(token);
        return res.status(200).json(authRes);
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, 'Internal Server Error');
    }
};

export const RevokeRefreshToken = async (req: Request, res: Response) => {
    try {
        const { userId } = req.body;
        await invitedUserFacade.revokeRefreshToken(userId);
        return res.status(204).send();
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, 'Internal Server Error');
    }
};
