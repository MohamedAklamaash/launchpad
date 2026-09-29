import { Router } from 'express';
import { LoginWithGitHub, GitHubCallback, GetCurrentUser } from '@/controllers/user.controller';

export const userRouter: Router = Router();

/**
 * @swagger
 * components:
 *   schemas:
 *     GitHubUser:
 *       type: object
 *       properties:
 *         id: { type: string }
 *         email: { type: string }
 *         user_name: { type: string }
 *         profile_url: { type: string }
 *         accessToken: { type: string }
 *         refreshToken: { type: string }
 *
 * /api/v1/user/login:
 *   get:
 *     summary: Initiate GitHub OAuth login (redirects to GitHub)
 *     description: >
 *       Generates a one-time, signed `state` value, binds it to the browser in a
 *       short-lived HttpOnly cookie, and includes it in the GitHub authorize URL
 *       (login-CSRF defense). Must be reached on the same origin the callback lands
 *       on (`GITHUB_REDIRECT_URI`), or the state cookie won't be present at /callback.
 *     tags: [GitHub OAuth]
 *     responses:
 *       302: { description: Redirect to GitHub authorization page, with a state cookie set }
 */
userRouter.get('/login', LoginWithGitHub);

/**
 * @swagger
 * /api/v1/user/callback:
 *   get:
 *     summary: GitHub OAuth callback — exchanges code for tokens and redirects to frontend
 *     description: >
 *       Requires `state` to match the state cookie set by /login (constant-time), be
 *       correctly signed, and be under 10 minutes old; the cookie is cleared on every
 *       call, so a state can only ever be presented once. No token exchange happens
 *       unless state validation passes.
 *     tags: [GitHub OAuth]
 *     parameters:
 *       - in: query
 *         name: code
 *         required: true
 *         schema: { type: string }
 *         description: Authorization code from GitHub
 *       - in: query
 *         name: state
 *         required: true
 *         schema: { type: string }
 *         description: Must match the state cookie set by /login
 *     responses:
 *       302:
 *         description: Redirects to frontend with access_token and refresh_token in the URL fragment (never the query string, so they aren't sent to any server or logged)
 *       400: { description: Missing code, or state missing/mismatched/expired/already used }
 */
userRouter.get('/callback', GitHubCallback);

/**
 * @swagger
 * /api/v1/user/me:
 *   get:
 *     summary: Get the currently authenticated user
 *     tags: [GitHub OAuth]
 *     security: [{ bearerAuth: [] }]
 *     responses:
 *       200:
 *         description: Current user profile
 *         content:
 *           application/json:
 *             schema: { $ref: '#/components/schemas/GitHubUser' }
 *       401: { description: No token or invalid token }
 */
userRouter.get('/me', GetCurrentUser);
