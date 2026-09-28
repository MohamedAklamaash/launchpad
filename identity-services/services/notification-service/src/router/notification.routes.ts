import { Router } from 'express';
import { GetMyNotifications } from '@/controllers/notification.controller';

export const notificationRouter: Router = Router();

/**
 * @swagger
 * components:
 *   schemas:
 *     Notification:
 *       type: object
 *       properties:
 *         _id: { type: string }
 *         user_id: { type: string }
 *         user_name: { type: string }
 *         email: { type: string }
 *         infra_id: { type: string }
 *         source: { type: string, example: provision_success }
 *         metadata: { type: object }
 *         created_at: { type: integer, description: Unix timestamp ms }
 *
 * /api/v1/notifications/me:
 *   get:
 *     summary: Get all notifications for the caller
 *     description: >
 *       There is no target-user parameter — the caller's identity comes only from the
 *       verified access token, so a caller can never read another user's notifications.
 *     tags: [Notifications]
 *     security: [{ bearerAuth: [] }]
 *     responses:
 *       200:
 *         description: List of notifications
 *         content:
 *           application/json:
 *             schema:
 *               type: array
 *               items: { $ref: '#/components/schemas/Notification' }
 *       401: { description: No valid access token }
 *       500: { description: Internal server error }
 */
notificationRouter.get('/me', GetMyNotifications);
