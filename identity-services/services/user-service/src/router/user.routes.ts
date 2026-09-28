import { Router } from 'express';
import { GetUserById, SearchUsers } from '@/controllers/user.controller';

export const userRouter: Router = Router();

/**
 * @swagger
 * components:
 *   schemas:
 *     User:
 *       type: object
 *       properties:
 *         user_id: { type: string }
 *         user_name: { type: string }
 *         email: { type: string }
 *         role: { type: string }
 *         profile_url: { type: string, nullable: true }
 *         infra_id: { type: array, items: { type: string } }
 *         invited_by: { type: string, nullable: true }
 *         created_at: { type: string, format: date-time }
 *         updated_at: { type: string, format: date-time }
 *
 * /api/v1/users/{userId}:
 *   get:
 *     summary: Get a user by ID — the caller may only fetch their own record
 *     tags: [Users]
 *     security: [{ bearerAuth: [] }]
 *     parameters:
 *       - in: path
 *         name: userId
 *         required: true
 *         schema: { type: string }
 *         example: 018e1234-abcd-7000-8000-000000000001
 *     responses:
 *       200:
 *         description: User found
 *         content:
 *           application/json:
 *             schema: { $ref: '#/components/schemas/User' }
 *       400: { description: User ID is required }
 *       401: { description: No valid access token }
 *       403: { description: userId does not match the caller's own id }
 *       404: { description: User not found }
 */
userRouter.get('/:userId', GetUserById);

/**
 * @swagger
 * components:
 *   schemas:
 *     UserSearchResult:
 *       type: object
 *       properties:
 *         user_id: { type: string }
 *         user_name: { type: string }
 *         email: { type: string }
 *         profile_url: { type: string, nullable: true }
 *
 * /api/v1/users:
 *   get:
 *     summary: Search users by username or email, scoped to the caller's own infras
 *     description: >
 *       Results are limited to users who share at least one infra with the caller (for
 *       inviting a member into one of the caller's own orgs) and to minimal fields —
 *       never infra_id, role, or invited_by.
 *     tags: [Users]
 *     security: [{ bearerAuth: [] }]
 *     parameters:
 *       - in: query
 *         name: q
 *         required: true
 *         schema: { type: string, minLength: 3 }
 *         description: Search term matched against user_name and email (minimum 3 characters)
 *         example: john
 *     responses:
 *       200:
 *         description: Matching users the caller shares an infra with
 *         content:
 *           application/json:
 *             schema:
 *               type: array
 *               items: { $ref: '#/components/schemas/UserSearchResult' }
 *       400: { description: Search query q is required or shorter than 3 characters }
 *       401: { description: No valid access token }
 */
userRouter.get('/', SearchUsers);
