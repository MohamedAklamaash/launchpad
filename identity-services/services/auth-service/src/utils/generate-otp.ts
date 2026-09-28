import { randomInt } from 'crypto';

// A registration/reset OTP gates account takeover if guessed, so each digit comes from
// Node's CSPRNG (randomInt), never Math.random() — which is a fast, non-cryptographic
// PRNG whose output is not meant to resist prediction.
export function generateOTP(length: number = 6): string {
    if (length <= 0) {
        throw new Error('OTP length must be greater than 0');
    }

    let otp = '';
    for (let i = 0; i < length; i++) {
        otp += randomInt(0, 10);
    }

    return otp;
}
