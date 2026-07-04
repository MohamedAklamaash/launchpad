import { renderEmail } from './layout';

export const getForgotPasswordTemplate = (otp: string, userName: string) =>
    renderEmail({
        preheader: 'Your Launchpad password reset code.',
        heading: 'Reset your password',
        intro: `Hi ${userName}, use the code below to reset your Launchpad password. It expires shortly. If you didn't request this, you can safely ignore this email.`,
        contentHtml: `<div style="margin:28px 0 0;padding:24px;text-align:center;background:#0e0e10;border:1px solid #26262b;border-radius:12px;">
            <div style="font-family:'SF Mono',Menlo,Consolas,monospace;font-size:34px;font-weight:700;letter-spacing:10px;color:#4aa8ff;">${otp}</div>
          </div>`,
    });
