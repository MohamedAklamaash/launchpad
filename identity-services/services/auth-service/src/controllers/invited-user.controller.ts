import { Request, Response } from 'express';
import { InvitedUserFacade } from '@/service/invited-user.facade.service';
import { HttpError } from '@launchpad/common';
import { USER_ROLE } from '@/types/auth.invited_user.types';
import { getAuthHeader } from '@/utils/auth-header';
import { verifySessionToken } from '@/utils/handle-token';
import { superAdminMiddleware } from '@/utils/super-admin';
import { resolveRevokeCallerId } from '@/utils/revoke-authz';
import { env } from '@/config/env';

const invitedUserFacade = new InvitedUserFacade();

export const RegisterInvitedUser = async (req: Request, res: Response) => {
    try {
        const token = getAuthHeader(req);
        const { email, password, user_name, infra_id, role } = req.body;
        const payload = verifySessionToken(token);
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
        const payload = verifySessionToken(token);
        const invitees = await invitedUserFacade.listInvitedBy(payload.sub);
        return res.status(200).json(
            invitees.map((u) => ({
                id: u.id,
                email: u.email,
                user_name: u.user_name,
                role: u.role,
                roles: u.roles,
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
        const payload = verifySessionToken(token);
        const super_user = await superAdminMiddleware(payload);
        const { userId } = req.params as { userId: string };
        const { infra_ids } = (req.body ?? {}) as { infra_ids?: string[] };
        if (infra_ids !== undefined && !Array.isArray(infra_ids)) {
            throw new HttpError(400, 'infra_ids must be an array');
        }
        const result = await invitedUserFacade.removeFromOrg(
            super_user.infra_id,
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

const authenticateOTP = async (email: string, otp: string, res: Response) => {
    const authRes = await invitedUserFacade.authenticateWithOTP({ email, otp });
    return res.status(200).json(authRes);
};

// GET stays only because it's the link auth-email.template.ts puts in the verification
// email (notification-service's user-event.consumer.ts builds
// `${GATEWAY_SERVICE_URL}/auth/authenticate-with-otp?email=...&otp=...`) — a clickable
// link has to be a GET. That still puts the OTP in the URL (access logs, browser
// history, proxies), so the dashboard's own manual-entry form uses the POST variant
// below instead; see AuthenticateOTPWithBody.
export const AuthenticateOTP = async (req: Request, res: Response) => {
    try {
        const { email, otp } = req.query as { email: string; otp: string };
        return await authenticateOTP(email, otp, res);
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, 'Internal Server Error');
    }
};

export const AuthenticateOTPWithBody = async (req: Request, res: Response) => {
    try {
        const { email, otp } = req.body as { email: string; otp: string };
        return await authenticateOTP(email, otp, res);
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, 'Internal Server Error');
    }
};

// Always responds the same way regardless of whether `email` belongs to an account —
// requestPasswordReset itself never throws for an unknown email or returns the OTP, and
// the response here doesn't wait for that work to finish, so a client can't distinguish
// "known email, email queued" from "unknown email, nothing happened" by status, body, or
// response time. Failures (DB down, queue unavailable) are logged, never surfaced to the
// caller — surfacing them would itself be a side channel.
export const ForgotPassword = async (req: Request, res: Response) => {
    const { email } = req.body;
    invitedUserFacade.forgotPassword({ email }).catch((error: unknown) => {
        console.error('ForgotPassword background task failed', error);
    });
    return res.status(202).json({
        message: 'If that email is registered, a verification code has been sent to it.',
    });
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

// The caller may only change their own password. `oldPassword` still proves possession
// of the current credential, but the account acted on comes from the verified access
// token's `sub` — never from a body-supplied email, which would let any bearer of a
// still-valid token (or a forged body on an unauthenticated call) target another account.
export const UpdatePassword = async (req: Request, res: Response) => {
    try {
        const token = getAuthHeader(req);
        const payload = verifySessionToken(token);
        const { oldPassword, newPassword } = req.body;
        const success = await invitedUserFacade.updatePassword({
            user_id: payload.sub,
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
        const { refreshToken } = (req.body ?? {}) as { refreshToken?: string };
        const userId = resolveRevokeCallerId(req.headers.authorization, refreshToken);
        await invitedUserFacade.revokeRefreshToken(userId);
        return res.status(204).send();
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, 'Internal Server Error');
    }
};
