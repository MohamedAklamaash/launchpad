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
 *     tags: [GitHub OAuth]
 *     responses:
 *       302: { description: Redirect to GitHub authorization page }
 */
userRouter.get('/login', LoginWithGitHub);

/**
 * @swagger
 * /api/v1/user/callback:
 *   get:
 *     summary: GitHub OAuth callback — exchanges code for tokens and redirects to frontend
 *     tags: [GitHub OAuth]
 *     parameters:
 *       - in: query
 *         name: code
 *         required: true
 *         schema: { type: string }
 *         description: Authorization code from GitHub
 *     responses:
 *       302:
 *         description: Redirects to frontend with access_token and refresh_token as query params
 *       400: { description: Missing code }
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
