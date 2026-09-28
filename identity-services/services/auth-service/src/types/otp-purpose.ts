// Single source of truth for the two flows that mint a UserOTP row, so the value that
// creates a code, the value that filters a lookup, and the value that keys the Redis
// attempt window can never drift apart into a typo'd mismatch.
export const OTP_PURPOSE = {
    REGISTER: 'register',
    PASSWORD_RESET: 'password-reset',
} as const;

export type OtpPurpose = (typeof OTP_PURPOSE)[keyof typeof OTP_PURPOSE];
