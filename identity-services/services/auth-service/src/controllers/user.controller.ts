import { Request, Response } from 'express';
import { UserFacadeService } from '@/service/user.facade.service';
import {
    clearOAuthStateCookie,
    generateOAuthState,
    isOAuthStateValid,
    readOAuthStateCookie,
    setOAuthStateCookie,
} from '@/utils/oauth-state';

const userService = new UserFacadeService();

export const LoginWithGitHub = async (req: Request, res: Response) => {
    const state = generateOAuthState();
    setOAuthStateCookie(req, res, state);
    const url = userService.getAuthUrl(state);
    return res.redirect(url);
};

export const GitHubCallback = async (req: Request, res: Response) => {
    const code = req.query.code as string | undefined;
    const state = req.query.state as string | undefined;
    const cookieState = readOAuthStateCookie(req);

    // The state (and the cookie carrying its counterpart) is single-use: clear it before
    // doing anything else so a replayed callback URL can never validate twice.
    clearOAuthStateCookie(res);

    if (!isOAuthStateValid(state, cookieState)) {
        return res.status(400).json({ message: 'Invalid or expired OAuth state' });
    }

    if (!code) return res.status(400).json({ message: 'Missing code' });

    try {
        const githubData = await userService.handleCallback({ code });
        const authResponse = await userService.upsertUser(githubData);

        const frontendUrl = process.env.FRONTEND_URL || 'http://localhost:3000';
        // Tokens go in the fragment, not the query string: a fragment is never sent to
        // any server (this one included, on the next navigation) and never appears in
        // Referer headers or access logs the way a query string does.
        const tokenParams = new URLSearchParams({
            access_token: authResponse.accessToken,
            refresh_token: authResponse.refreshToken,
        });
        const redirectUrl = `${frontendUrl}/auth/callback#${tokenParams.toString()}`;

        return res.redirect(redirectUrl);
    } catch (err: unknown) {
        const frontendUrl = process.env.FRONTEND_URL || 'http://localhost:3000';
        if (err instanceof Error) {
            return res.redirect(
                `${frontendUrl}/auth/callback?error=${encodeURIComponent(err.message)}`,
            );
        }
        return res.redirect(
            `${frontendUrl}/auth/callback?error=${encodeURIComponent('Invalid token')}`,
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
    } catch (err: unknown) {
        if (err instanceof Error) {
            return res.status(401).json({ error: err.message || 'Invalid token' });
        }
        return res.status(401).json({ error: 'Invalid token' });
    }
};
