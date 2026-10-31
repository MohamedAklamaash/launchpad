export const MAX_DISPLAY_NAME_LENGTH = 64;

// Org and inviter names are attacker-controlled text inside a Launchpad-branded email.
// Keep letters, digits, spaces and a few harmless marks, and drop anything that lets a mail
// client auto-link text (":", "/", ".", "@"), so a name can't smuggle in a clickable URL.
// Organization names are stored through this same function, so the stored name is exactly
// what the email renders.
export const sanitizeDisplayName = (value: string): string =>
    value
        .normalize('NFKC')
        .replace(/[^\p{L}\p{N} '&(),_-]/gu, ' ')
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, MAX_DISPLAY_NAME_LENGTH)
        .trim();
