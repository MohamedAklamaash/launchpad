import { renderEmail } from './layout';

export const getAuthEmailTemplate = (url: string, userName: string) =>
    renderEmail({
        preheader: 'Confirm your email to activate your Launchpad account.',
        eyebrow: 'Account access',
        heading: `Welcome aboard, ${userName}`,
        intro: "You've been invited to Launchpad. Confirm your email to activate your account and start shipping to your own AWS.",
        cta: { label: 'Activate my account', url },
        contentHtml: `<p style="margin:24px 0 0;font-size:13px;line-height:1.6;color:#71717a;">This sign-in link is single-use and expires shortly. If the button doesn't work, paste this into your browser:</p>
          <p style="margin:8px 0 0;font-size:13px;word-break:break-all;"><a href="${url}" style="color:#4aa8ff;text-decoration:none;">${url}</a></p>
          <p style="margin:20px 0 0;font-size:13px;line-height:1.6;color:#71717a;">Didn't expect this invite? You can safely ignore this email &mdash; no account is created until you confirm.</p>`,
    });
