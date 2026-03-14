import { z } from '@launchpad/common';
import { USER_ROLE } from '@/types/auth.invited_user.types';

export const registerSchema = z.object({
    body: z.object({
        email: z.email(),
        password: z.string().min(6),
        user_name: z.string().min(3),
        infra_id: z.uuid(),
        role: z.enum(USER_ROLE)
    }),
});

export const loginSchema = z.object({
    body: z.object({
        email: z.email(),
        password: z.string().min(6),
    }),
});

export const otpSchema = z.object({
    query: z.object({
        email: z.email(),
        otp: z.string().length(6),
    }),
});

export const forgotPasswordSchema = z.object({
    body: z.object({
        email: z.email(),
    }),
});

export const verifyResetSchema = z.object({
    body: z.object({
        email: z.email(),
        otp: z.string().length(6),
    }),
});

export const resetPasswordSchema = z.object({
    body: z.object({
        token: z.string(),
        newPassword: z.string().min(6),
    }),
});

export const updatePasswordSchema = z.object({
    body: z.object({
        email: z.email(),
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
