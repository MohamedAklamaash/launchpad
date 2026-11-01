const OFF_VALUES = ['false', '0', 'no', 'off'];

// On unless explicitly off (false/0/no/off, any case); unset and empty are on. Same rule as
// the backend services.
export const parseOrgsEnabled = (value: string | undefined): boolean =>
    value === undefined || !OFF_VALUES.includes(value.trim().toLowerCase());

// Keep in lockstep with the backend's ORGS_ENABLED. With it off the dashboard behaves
// exactly as it did before F9: no org switcher, no members page, legacy role checks.
export const ORGS_ENABLED = parseOrgsEnabled(process.env.NEXT_PUBLIC_ORGS_ENABLED);
