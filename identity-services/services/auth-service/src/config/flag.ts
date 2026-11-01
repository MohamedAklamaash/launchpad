const OFF_VALUES = new Set(['false', '0', 'no', 'off']);

// On unless explicitly off. Unset and empty are on; same rule as the Django services and the
// frontend.
export const parseFlagDefaultOn = (v: unknown): boolean =>
    v === undefined || v === null || !OFF_VALUES.has(String(v).trim().toLowerCase());
