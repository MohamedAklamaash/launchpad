import { Router } from 'express';
import { validateRequest } from '@launchpad/common';
import {
    RegisterInvitedUser,
    LoginUser,
    AuthenticateOTP,
    ForgotPassword,
    VerifyResetOTP,
    ResetPassword,
    UpdatePassword,
    RefreshTokenForUser,
    RevokeRefreshToken,
} from '@/controllers/invited-user.controller';
import {
    registerSchema,
    loginSchema,
    otpSchema,
    forgotPasswordSchema,
    verifyResetSchema,
    resetPasswordSchema,
    updatePasswordSchema,
    refreshSchema,
    revokeSchema,
} from '@/schemas/invited-user.schema';

export const authRouter: Router = Router();

/**
 * @swagger
 * components:
 *   schemas:
 *     AuthTokens:
 *       type: object
 *       properties:
 *         accessToken: { type: string }
 *         refreshToken: { type: string }
 *     SuccessBoolean:
 *       type: object
 *       properties:
 *         success: { type: boolean }
 *     Error:
 *       type: object
 *       properties:
 *         message: { type: string }
 *
 * /api/v1/auth/register:
 *   post:
 *     summary: Register an invited user
 *     tags: [Auth]
 *     security: [{ bearerAuth: [] }]
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required: [email, password, user_name, infra_id, role]
 *             properties:
 *               email: { type: string, format: email, example: user@example.com }
 *               password: { type: string, minLength: 6, example: secret123 }
 *               user_name: { type: string, minLength: 3, example: johndoe }
 *               infra_id: { type: string, format: uuid, example: 018e1234-abcd-7000-8000-000000000001 }
 *               role: { type: string, enum: [ADMIN, USER], example: USER }
 *     responses:
 *       201:
 *         description: User registered successfully
 *         content:
 *           application/json:
 *             schema: { $ref: '#/components/schemas/AuthTokens' }
 *       401: { description: Unauthorized — caller is not a super admin or not authorized for this infra }
 *       400: { description: Validation error }
 */
authRouter.post(
    '/register',
    validateRequest({ body: registerSchema.shape.body }),
    RegisterInvitedUser,
);

/**
 * @swagger
 * /api/v1/auth/login:
 *   post:
 *     summary: Login with email and password
 *     tags: [Auth]
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required: [email, password]
 *             properties:
 *               email: { type: string, format: email, example: user@example.com }
 *               password: { type: string, minLength: 6, example: secret123 }
 *     responses:
 *       200:
 *         description: Login successful
 *         content:
 *           application/json:
 *             schema: { $ref: '#/components/schemas/AuthTokens' }
 *       401: { description: Invalid credentials }
 */
authRouter.post('/login', validateRequest({ body: loginSchema.shape.body }), LoginUser);

/**
 * @swagger
 * /api/v1/auth/authenticate-with-otp:
 *   get:
 *     summary: Verify email OTP after registration
 *     tags: [Auth]
 *     parameters:
 *       - in: query
 *         name: email
 *         required: true
 *         schema: { type: string, format: email }
 *         example: user@example.com
 *       - in: query
 *         name: otp
 *         required: true
 *         schema: { type: string, minLength: 6, maxLength: 6 }
 *         example: "123456"
 *     responses:
 *       200:
 *         description: OTP verified, returns tokens
 *         content:
 *           application/json:
 *             schema: { $ref: '#/components/schemas/AuthTokens' }
 *       401: { description: Invalid or expired OTP }
 */
authRouter.get(
    '/authenticate-with-otp',
    validateRequest({ query: otpSchema.shape.query }),
    AuthenticateOTP,
);

/**
 * @swagger
 * /api/v1/auth/forgot-password:
 *   post:
 *     summary: Request a password reset OTP via email
 *     tags: [Auth]
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required: [email]
 *             properties:
 *               email: { type: string, format: email, example: user@example.com }
 *     responses:
 *       200:
 *         description: OTP sent to email
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 otp: { type: string, example: "482910" }
 */
authRouter.post(
    '/forgot-password',
    validateRequest({ body: forgotPasswordSchema.shape.body }),
    ForgotPassword,
);

/**
 * @swagger
 * /api/v1/auth/verify-reset-otp:
 *   post:
 *     summary: Verify password reset OTP and get a reset token
 *     tags: [Auth]
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required: [email, otp]
 *             properties:
 *               email: { type: string, format: email, example: user@example.com }
 *               otp: { type: string, minLength: 6, maxLength: 6, example: "482910" }
 *     responses:
 *       200:
 *         description: OTP verified
 *         content:
 *           application/json:
 *             schema: { $ref: '#/components/schemas/SuccessBoolean' }
 *       401: { description: Invalid OTP }
 */
authRouter.post(
    '/verify-reset-otp',
    validateRequest({ body: verifyResetSchema.shape.body }),
    VerifyResetOTP,
);

/**
 * @swagger
 * /api/v1/auth/reset-password:
 *   post:
 *     summary: Reset password using the reset token
 *     tags: [Auth]
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required: [token, newPassword]
 *             properties:
 *               token: { type: string, example: eyJhbGciOiJIUzI1NiJ9... }
 *               newPassword: { type: string, minLength: 6, example: newSecret123 }
 *     responses:
 *       200:
 *         description: Password reset successful
 *         content:
 *           application/json:
 *             schema: { $ref: '#/components/schemas/SuccessBoolean' }
 */
authRouter.post(
    '/reset-password',
    validateRequest({ body: resetPasswordSchema.shape.body }),
    ResetPassword,
);

/**
 * @swagger
 * /api/v1/auth/update-password:
 *   post:
 *     summary: Update password (authenticated)
 *     tags: [Auth]
 *     security: [{ bearerAuth: [] }]
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required: [email, oldPassword, newPassword]
 *             properties:
 *               email: { type: string, format: email, example: user@example.com }
 *               oldPassword: { type: string, minLength: 6, example: oldSecret123 }
 *               newPassword: { type: string, minLength: 6, example: newSecret456 }
 *     responses:
 *       200:
 *         description: Password updated
 *         content:
 *           application/json:
 *             schema: { $ref: '#/components/schemas/SuccessBoolean' }
 *       401: { description: Wrong old password }
 */
authRouter.post(
    '/update-password',
    validateRequest({ body: updatePasswordSchema.shape.body }),
    UpdatePassword,
);

/**
 * @swagger
 * /api/v1/auth/refresh:
 *   post:
 *     summary: Refresh access token using refresh token
 *     tags: [Auth]
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required: [token]
 *             properties:
 *               token: { type: string, example: eyJhbGciOiJIUzI1NiJ9... }
 *     responses:
 *       200:
 *         description: New access token issued
 *         content:
 *           application/json:
 *             schema: { $ref: '#/components/schemas/AuthTokens' }
 *       401: { description: Invalid or expired refresh token }
 */
authRouter.post(
    '/refresh',
    validateRequest({ body: refreshSchema.shape.body }),
    RefreshTokenForUser,
);

/**
 * @swagger
 * /api/v1/auth/revoke:
 *   post:
 *     summary: Revoke all refresh tokens for a user
 *     tags: [Auth]
 *     security: [{ bearerAuth: [] }]
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required: [userId]
 *             properties:
 *               userId: { type: string, format: uuid, example: 018e1234-abcd-7000-8000-000000000001 }
 *     responses:
 *       204: { description: Tokens revoked }
 *       401: { description: Unauthorized }
 */
authRouter.post('/revoke', validateRequest({ body: revokeSchema.shape.body }), RevokeRefreshToken);
