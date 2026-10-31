import { renderEmail } from './layout';

// The organization name and the inviter's display name are customer-supplied and land
// directly in this HTML email. renderEmail interpolates its inputs verbatim, so every
// value is escaped here before it gets there.
const escapeHtml = (value: string): string =>
    value
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');

const ROLE_LABELS: Record<string, string> = {
    admin: 'Admin',
    developer: 'Developer',
    viewer: 'Viewer',
};

const MAX_DISPLAY_NAME_LENGTH = 64;

// Org and inviter names are attacker-controlled text inside a Launchpad-branded email.
// Keep letters, digits, spaces and a few harmless marks, and drop anything that lets a mail
// client auto-link text (":", "/", ".", "@"), so a name can't smuggle in a clickable URL.
export const sanitizeDisplayName = (value: string): string =>
    value
        .normalize('NFKC')
        .replace(/[^\p{L}\p{N} '&(),_-]/gu, ' ')
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, MAX_DISPLAY_NAME_LENGTH)
        .trim();

export const getInvitationSubject = (organizationName: string): string => {
    const name = sanitizeDisplayName(organizationName) || 'a team';
    return `You've been invited to join ${name} on Launchpad`;
};

// DASHBOARD_URL is configured with a path (".../dashboard"), so only its origin is used.
// The token rides in the URL fragment: browsers never send a fragment to any server, so
// it stays out of the dashboard's and the gateway's access logs and out of Referer headers.
// Returns undefined for anything that is not a plain http(s) URL.
export const buildInvitationAcceptUrl = (
    dashboardUrl: string,
    token: string,
): string | undefined => {
    let base: URL;
    try {
        base = new URL(dashboardUrl);
    } catch {
        return undefined;
    }
    if (base.protocol !== 'https:' && base.protocol !== 'http:') return undefined;
    const link = new URL('/invitations/accept', base.origin);
    link.hash = `token=${encodeURIComponent(token)}`;
    return link.toString();
};

export interface InvitationTemplateInput {
    organizationName: string;
    inviterName: string;
    role: string;
    expiresAt: Date;
    acceptUrl: string;
}

export const getInvitationTemplate = (input: InvitationTemplateInput): string => {
    const organization = escapeHtml(sanitizeDisplayName(input.organizationName) || 'a team');
    const inviter = escapeHtml(sanitizeDisplayName(input.inviterName) || 'A teammate');
    const role = escapeHtml(ROLE_LABELS[input.role] ?? 'Member');
    const expires = escapeHtml(input.expiresAt.toUTCString());
    const url = escapeHtml(input.acceptUrl);

    return renderEmail({
        preheader: `${inviter} invited you to join ${organization} on Launchpad.`,
        eyebrow: 'Organization · Invitation',
        heading: "You've been invited to join a team",
        intro: `${inviter} invited you to join <strong style="color:#f4f4f5;">${organization}</strong> on Launchpad as <strong style="color:#f4f4f5;">${role}</strong>.`,
        cta: { label: 'Accept invitation', url },
        contentHtml: `<p style="margin:24px 0 0;font-size:13px;line-height:1.6;color:#8a8a94;">Sign in with the email address this invitation was sent to, then accept. It is single-use and expires on ${expires}. If the button doesn't work, paste this into your browser:</p>
          <p style="margin:8px 0 0;font-size:13px;word-break:break-all;"><a href="${url}" style="color:#4aa8ff;text-decoration:none;">${url}</a></p>
          <p style="margin:20px 0 0;font-size:13px;line-height:1.6;color:#8a8a94;">Didn't expect this invitation? You can safely ignore this email &mdash; nothing happens until it is accepted.</p>`,
    });
};
