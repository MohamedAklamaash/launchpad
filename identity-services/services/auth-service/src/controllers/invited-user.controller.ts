import { Request, Response } from "express";
import { InvitedUserFacade } from "@/service/invited-user.facade.service";
import { HttpError } from "@launchpad/common";
import { USER_ROLE } from "@/types/auth.invited_user.types";
import { getAuthHeader } from "@/utils/auth-header";
import { verifyAccessToken } from "@/utils/handle-token";
import { superAdminMiddleware } from "@/utils/super-admin";

const invitedUserFacade = new InvitedUserFacade();

export const RegisterInvitedUser = async (req: Request, res: Response) => {
    try {
        const token = getAuthHeader(req);
        const { email, password, user_name, infra_id, role } = req.body;
        const payload = verifyAccessToken(token);
        const super_user = await superAdminMiddleware(payload);
        if (!super_user.infra_id.includes(infra_id)) {
            throw new HttpError(401, `Unauthorized, user:${payload.user_name} is not authorized to invite users to ${infra_id}`);
        }
        const authRes = await invitedUserFacade.register({
            email,
            password,
            user_name,
            infra_id,
            role: role as USER_ROLE,
        }, super_user.id);

        return res.status(201).json(authRes);
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, "Internal Server Error");
    }
};

export const LoginUser = async (req: Request, res: Response) => {
    try {
        const { email, password, infra_id } = req.body;
        const authRes = await invitedUserFacade.login({ email, password, infra_id });
        return res.status(200).json(authRes);
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, "Internal Server Error");
    }
};

export const AuthenticateOTP = async (req: Request, res: Response) => {
    try {
        const { email, otp } = req.query as { email: string; otp: string };
        const authRes = await invitedUserFacade.authenticateWithOTP({
            email,
            otp
        });

        return res.status(200).json(authRes);
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, "Internal Server Error");
    }
};

export const ForgotPassword = async (req: Request, res: Response) => {
    try {
        const { email } = req.body;
        const otp = await invitedUserFacade.forgotPassword({ email });
        return res.status(200).json({ otp });
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, "Internal Server Error");
    }
};

export const VerifyResetOTP = async (req: Request, res: Response) => {
    try {
        const { email, otp } = req.body;
        const success = await invitedUserFacade.verifyResetOTP({
            email,
            otp
        });
        return res.status(200).json({ success });
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, "Internal Server Error");
    }
};

export const ResetPassword = async (req: Request, res: Response) => {
    try {
        const { token, newPassword } = req.body;
        const success = await invitedUserFacade.resetPassword({
            reset_token: token,
            new_password: newPassword
        });
        return res.status(200).json({ success });
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, "Internal Server Error");
    }
};

export const UpdatePassword = async (req: Request, res: Response) => {
    try {
        const { email, oldPassword, newPassword } = req.body;
        const success = await invitedUserFacade.updatePassword({
            email,
            old_password: oldPassword,
            new_password: newPassword
        });

        return res.status(200).json({ success });
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, "Internal Server Error");
    }
};

export const RefreshTokenForUser = async (req: Request, res: Response) => {
    try {
        const { token } = req.body;
        const authRes = await invitedUserFacade.refresh(token);
        return res.status(200).json(authRes);
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, "Internal Server Error");
    }
};

export const RevokeRefreshToken = async (req: Request, res: Response) => {
    try {
        const { userId } = req.body;
        await invitedUserFacade.revokeRefreshToken(userId);
        return res.status(204).send();
    } catch (error: unknown) {
        if (error instanceof HttpError) throw error;
        throw new HttpError(500, "Internal Server Error");
    }
};
