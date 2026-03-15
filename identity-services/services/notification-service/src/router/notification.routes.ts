import { Router } from "express";
import { GetUserNotifications } from "@/controllers/notification.controller";

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
 * /api/v1/notifications/user/{userId}:
 *   get:
 *     summary: Get all notifications for a user
 *     tags: [Notifications]
 *     security: [{ bearerAuth: [] }]
 *     parameters:
 *       - in: path
 *         name: userId
 *         required: true
 *         schema: { type: string }
 *         example: 018e1234-abcd-7000-8000-000000000001
 *     responses:
 *       200:
 *         description: List of notifications
 *         content:
 *           application/json:
 *             schema:
 *               type: array
 *               items: { $ref: '#/components/schemas/Notification' }
 *       400: { description: User ID is required }
 *       500: { description: Internal server error }
 */
notificationRouter.get("/user/:userId", GetUserNotifications);
