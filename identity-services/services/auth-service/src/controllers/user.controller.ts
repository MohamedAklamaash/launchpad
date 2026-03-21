import { Request, Response } from 'express';
import { UserFacadeService } from '@/service/user.facade.service';

const userService = new UserFacadeService();

export const LoginWithGitHub = async (_req: Request, res: Response) => {
    const url = userService.getAuthUrl();
    return res.redirect(url);
};

export const GitHubCallback = async (req: Request, res: Response) => {
    const code = req.query.code as string;
    if (!code) return res.status(400).json({ message: 'Missing code' });

    try {
        const githubData = await userService.handleCallback({ code });
        const authResponse = await userService.upsertUser(githubData);

        const frontendUrl = process.env.FRONTEND_URL || 'http://localhost:3000';
        const redirectUrl = `${frontendUrl}/auth/callback?access_token=${authResponse.accessToken}&refresh_token=${authResponse.refreshToken}`;

        return res.redirect(redirectUrl);
    } catch (err: any) {
        const frontendUrl = process.env.FRONTEND_URL || 'http://localhost:3000';
        return res.redirect(
            `${frontendUrl}/auth/callback?error=${encodeURIComponent(err.message)}`,
        );
    }
};

export const GetCurrentUser = async (req: Request, res: Response) => {
    try {
        const token = req.headers.authorization?.replace('Bearer ', '');
        if (!token) {
            return res.status(401).json({ error: 'No token provided' });
        }

        const user = await userService.getUserFromToken(token);
        return res.status(200).json(user);
    } catch (err: any) {
        return res.status(401).json({ error: err.message || 'Invalid token' });
    }
};
