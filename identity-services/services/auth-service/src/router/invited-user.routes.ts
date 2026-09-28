import { Router } from 'express';
import { validateRequest } from '@launchpad/common';
import {
    RegisterInvitedUser,
    ListInvitedUsers,
    RemoveMemberFromOrg,
    LoginUser,
    AuthenticateOTP,
    AuthenticateOTPWithBody,
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
 * /api/v1/auth/invited-users:
 *   get:
 *     summary: List users the caller has invited, with verification status
 *     tags: [Auth]
 *     security: [{ bearerAuth: [] }]
 *     responses:
 *       200:
 *         description: Invited users (id, email, user_name, role, infra_id, is_authenticated, created_at)
 *       401: { description: Unauthorized }
 */
authRouter.get('/invited-users', ListInvitedUsers);

/**
 * @swagger
 * /api/v1/auth/invited-users/{userId}:
 *   delete:
 *     summary: Remove a member from one or more orgs; deletes their account only if it was their last org
 *     tags: [Auth]
 *     security: [{ bearerAuth: [] }]
 *     parameters:
 *       - in: path
 *         name: userId
 *         required: true
 *         schema: { type: string, format: uuid }
 *     requestBody:
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               infra_ids: { type: array, items: { type: string, format: uuid } }
 *     responses:
 *       200: { description: "{ removed, deleted_account }" }
 *       403: { description: Not permitted by role hierarchy }
 *       404: { description: Member not found }
 */
authRouter.delete('/invited-users/:userId', RemoveMemberFromOrg);

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
 *     summary: Verify email OTP after registration (magic-link form)
 *     description: >
 *       Only the link in the verification email should use this — a clickable email
 *       link has to be a GET, which means the OTP sits in the URL (access logs,
 *       browser history, proxies). A form or script submitting the OTP directly
 *       should use `POST /authenticate-with-otp` instead.
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
 *       400: { description: Invalid or expired OTP }
 *       429: { description: Too many failed attempts — request a new code }
 */
authRouter.get(
    '/authenticate-with-otp',
    validateRequest({ query: otpSchema.shape.query }),
    AuthenticateOTP,
);

/**
 * @swagger
 * /api/v1/auth/authenticate-with-otp:
 *   post:
 *     summary: Verify email OTP after registration
 *     description: >
 *       Preferred over the GET variant for anything that isn't a clicked email link —
 *       keeps the OTP out of the URL/access logs.
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
 *               otp: { type: string, minLength: 6, maxLength: 6, example: "123456" }
 *     responses:
 *       200:
 *         description: OTP verified, returns tokens
 *         content:
 *           application/json:
 *             schema: { $ref: '#/components/schemas/AuthTokens' }
 *       400: { description: Invalid or expired OTP }
 *       429: { description: Too many failed attempts — request a new code }
 */
authRouter.post(
    '/authenticate-with-otp',
    validateRequest({ body: otpSchema.shape.query }),
    AuthenticateOTPWithBody,
);

/**
 * @swagger
 * /api/v1/auth/forgot-password:
 *   post:
 *     summary: Request a password reset code via email
 *     description: >
 *       Always responds the same way whether or not `email` belongs to an account —
 *       the response never reveals account existence, and never contains the code.
 *       The code (if any) is only ever delivered by email.
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
 *       202:
 *         description: Generic acknowledgement — identical for a known or unknown email
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 message: { type: string }
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
 *       400: { description: Invalid or expired OTP }
 *       429: { description: Too many failed attempts — request a new code }
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
 *     summary: Update the caller's own password
 *     description: >
 *       The account acted on is always the caller from the verified access token —
 *       there is no way to target another user's password.
 *     tags: [Auth]
 *     security: [{ bearerAuth: [] }]
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required: [oldPassword, newPassword]
 *             properties:
 *               oldPassword: { type: string, minLength: 6, example: oldSecret123 }
 *               newPassword: { type: string, minLength: 6, example: newSecret456 }
 *     responses:
 *       200:
 *         description: Password updated
 *         content:
 *           application/json:
 *             schema: { $ref: '#/components/schemas/SuccessBoolean' }
 *       401: { description: No valid access token, or wrong old password }
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
 *     summary: Revoke all of the caller's own refresh tokens
 *     description: >
 *       Revokes every refresh token for the authenticated caller only — there is no
 *       parameter to target another user. Accepts either an `Authorization: Bearer`
 *       access token (signature-valid and unexpired; a stale auth_time is fine) or a
 *       refresh token in the body as proof of possession.
 *     tags: [Auth]
 *     security: [{ bearerAuth: [] }]
 *     requestBody:
 *       required: false
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             properties:
 *               refreshToken: { type: string, example: eyJhbGciOiJIUzI1NiJ9... }
 *     responses:
 *       204: { description: Tokens revoked }
 *       401: { description: No valid access token or refresh token was presented }
 */
authRouter.post('/revoke', validateRequest({ body: revokeSchema.shape.body }), RevokeRefreshToken);
