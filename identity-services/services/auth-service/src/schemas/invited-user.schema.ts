import { z } from '@launchpad/common';
import { USER_ROLE } from '@/types/auth.invited_user.types';

export const registerSchema = z.object({
    body: z.object({
        email: z.email(),
        password: z.string().min(6),
        userName: z.string().min(3),
        infraId: z.uuid(),
        role: z.enum(USER_ROLE)
    }),
});

export const loginSchema = z.object({
    body: z.object({
        email: z.email(),
        password: z.string().min(6),
        infraId: z.uuid(),
    }),
});

export const otpSchema = z.object({
    body: z.object({
        userId: z.uuid(),
        otp: z.string().length(6),
        infraId: z.uuid(),
    }),
});

export const forgotPasswordSchema = z.object({
    body: z.object({
        email: z.email(),
        infraId: z.uuid(),
    }),
});

export const verifyResetSchema = z.object({
    body: z.object({
        userId: z.uuid(),
        otp: z.string().length(6),
        infraId: z.uuid(),
    }),
});

export const resetPasswordSchema = z.object({
    body: z.object({
        userId: z.uuid(),
        newPassword: z.string().min(6),
    }),
});

export const updatePasswordSchema = z.object({
    body: z.object({
        userId: z.uuid(),
        oldPassword: z.string().min(6),
        newPassword: z.string().min(6),
    }),
});

export const refreshSchema = z.object({
    body: z.object({
        token: z.string(),
    }),
});

export const revokeSchema = z.object({
    body: z.object({
        userId: z.uuid(),
    }),
});
