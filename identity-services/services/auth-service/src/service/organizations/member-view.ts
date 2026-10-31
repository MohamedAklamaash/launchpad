import { sanitizeDisplayName } from '@launchpad/common';
import { ORG_ROLE, type OrgRole } from '@/types/org-role';

const ANONYMOUS_NAME = 'Member';

export const canSeeEmails = (role: OrgRole): boolean => role !== ORG_ROLE.VIEWER;

// Names are user-typed text (often an email). Everyone gets the sanitized form; a viewer,
// who must not learn addresses, gets a neutral name for anything that looked like one.
export const displayNameFor = (raw: string | null | undefined, viewerRole: OrgRole): string => {
    if (viewerRole === ORG_ROLE.VIEWER && raw?.includes('@')) return ANONYMOUS_NAME;
    return sanitizeDisplayName(raw ?? '') || ANONYMOUS_NAME;
};
