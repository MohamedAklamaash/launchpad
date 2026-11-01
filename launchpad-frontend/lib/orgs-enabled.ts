// Keep in lockstep with the backend's ORGS_ENABLED. On unless explicitly "false"; with it
// off the dashboard behaves exactly as it did before F9: no org switcher, no members page,
// legacy role checks.
export const ORGS_ENABLED = process.env.NEXT_PUBLIC_ORGS_ENABLED !== 'false';
