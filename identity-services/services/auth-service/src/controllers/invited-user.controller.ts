import { Request, Response } from "express";
import { InvitedUserFacade } from "@/service/invited-user.facade.service";

const invitedUserFacade = new InvitedUserFacade();

export const RegisterInvitedUser = async (req: Request, res: Response) => {
    try {
        const { email, password, userName, infraId, role } = req.body;
        const authRes = await invitedUserFacade.register(email, password, userName, infraId, role);
        return res.status(201).json(authRes);
    } catch (err: any) {
        return res.status(err.status || 500).json({ message: err.message });
    }
};

export const LoginUser = async (req: Request, res: Response) => {
    try {
        const { email, password, infraId } = req.body;
        const authRes = await invitedUserFacade.login(email, password, infraId);
        return res.status(200).json(authRes);
    } catch (err: any) {
        return res.status(err.status || 500).json({ message: err.message });
    }
};

export const AuthenticateOTP = async (req: Request, res: Response) => {
    try {
        const { userId, otp, infraId } = req.body;
        const authRes = await invitedUserFacade.authenticateWithOTP(userId, otp, infraId);
        return res.status(200).json(authRes);
    } catch (err: any) {
        return res.status(err.status || 500).json({ message: err.message });
    }
};

export const ForgotPassword = async (req: Request, res: Response) => {
    try {
        const { email, infraId } = req.body;
        const otp = await invitedUserFacade.forgotPassword(email, infraId);
        return res.status(200).json({ otp });
    } catch (err: any) {
        return res.status(err.status || 500).json({ message: err.message });
    }
};

export const VerifyResetOTP = async (req: Request, res: Response) => {
    try {
        const { userId, infraId, otp } = req.body;
        const success = await invitedUserFacade.verifyResetOTP(userId, infraId, otp);
        return res.status(200).json({ success });
    } catch (err: any) {
        return res.status(err.status || 500).json({ message: err.message });
    }
};

export const ResetPassword = async (req: Request, res: Response) => {
    try {
        const { userId, newPassword } = req.body;
        const success = await invitedUserFacade.resetPassword(userId, newPassword);
        return res.status(200).json({ success });
    } catch (err: any) {
        return res.status(err.status || 500).json({ message: err.message });
    }
};

export const UpdatePassword = async (req: Request, res: Response) => {
    try {
        const { userId, oldPassword, newPassword } = req.body;
        const success = await invitedUserFacade.updatePassword(userId, oldPassword, newPassword);
        return res.status(200).json({ success });
    } catch (err: any) {
        return res.status(err.status || 500).json({ message: err.message });
    }
};

export const RefreshTokenForUser = async (req: Request, res: Response) => {
    try {
        const { token } = req.body;
        const authRes = await invitedUserFacade.refresh(token);
        return res.status(200).json(authRes);
    } catch (err: any) {
        return res.status(err.status || 500).json({ message: err.message });
    }
};

export const RevokeRefreshToken = async (req: Request, res: Response) => {
    const { userId } = req.body;
    await invitedUserFacade.revokeRefreshToken(userId);
    return res.status(204).send();
};

