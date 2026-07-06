import { renderEmail } from './layout';

export const getForgotPasswordTemplate = (otp: string, userName: string) =>
    renderEmail({
        preheader: 'Your Launchpad password reset code.',
        eyebrow: 'Security',
        heading: 'Reset your password',
        intro: `Hi ${userName}, use the code below to reset your Launchpad password. If you didn't request this, you can safely ignore this email.`,
        contentHtml: `<div style="margin:28px 0 0;padding:26px;text-align:center;background:#0e0e10;border:1px solid #26262b;border-radius:12px;">
            <div style="font-family:'SF Mono',SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;font-weight:600;letter-spacing:2px;color:#8a8a94;text-transform:uppercase;margin:0 0 12px;">Reset code</div>
            <div style="font-family:'SF Mono',SFMono-Regular,Menlo,Consolas,monospace;font-size:34px;font-weight:700;letter-spacing:10px;color:#4aa8ff;">${otp}</div>
          </div>
          <p style="margin:16px 0 0;font-size:13px;line-height:1.6;color:#8a8a94;">This code expires shortly and can only be used once. Never share it &mdash; Launchpad staff will never ask for it.</p>`,
    });
