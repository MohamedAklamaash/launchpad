
export function generateOTP(length: number = 6): string {
    if (length <= 0) {
        throw new Error("OTP length must be greater than 0");
    }

    let otp = "";
    for (let i = 0; i < length; i++) {
        otp += Math.floor(Math.random() * 10);
    }

    return otp;
}
