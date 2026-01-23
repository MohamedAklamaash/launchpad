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
    RevokeRefreshToken
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
    revokeSchema
} from '@/schemas/invited-user.schema';

export const authRouter: Router = Router();

authRouter.post(
    '/register',
    validateRequest({ body: registerSchema.shape.body }),
    RegisterInvitedUser
);

authRouter.post(
    '/login',
    validateRequest({ body: loginSchema.shape.body }),
    LoginUser
);

authRouter.post(
    '/otp/authenticate',
    validateRequest({ body: otpSchema.shape.body }),
    AuthenticateOTP
);

authRouter.post(
    '/forgot-password',
    validateRequest({ body: forgotPasswordSchema.shape.body }),
    ForgotPassword
);

authRouter.post(
    '/verify-reset-otp',
    validateRequest({ body: verifyResetSchema.shape.body }),
    VerifyResetOTP
);

authRouter.post(
    '/reset-password',
    validateRequest({ body: resetPasswordSchema.shape.body }),
    ResetPassword
);

authRouter.post(
    '/update-password',
    validateRequest({ body: updatePasswordSchema.shape.body }),
    UpdatePassword
);

authRouter.post(
    '/refresh',
    validateRequest({ body: refreshSchema.shape.body }),
    RefreshTokenForUser
);

authRouter.post(
    '/revoke',
    validateRequest({ body: revokeSchema.shape.body }),
    RevokeRefreshToken
);
