import { renderEmail } from './layout';

export const getAuthEmailTemplate = (url: string, userName: string) =>
    renderEmail({
        preheader: 'Confirm your email to activate your Launchpad account.',
        heading: `Welcome, ${userName}`,
        intro: "You've been invited to Launchpad. Confirm your email to activate your account and start shipping to your own AWS.",
        cta: { label: 'Authenticate & sign in', url },
        contentHtml: `<p style="margin:24px 0 0;font-size:13px;line-height:1.6;color:#71717a;">If the button doesn't work, paste this link into your browser:</p>
          <p style="margin:6px 0 0;font-size:13px;word-break:break-all;"><a href="${url}" style="color:#4aa8ff;text-decoration:none;">${url}</a></p>`,
    });
