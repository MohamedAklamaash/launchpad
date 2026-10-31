// A newly created or joined org reaches the infrastructure, application and payment
// services through an async event, so for a moment after switching into it their org
// lookups answer 404. The switch marks a short window; inside it, a failed GET is retried
// with backoff instead of showing an error. The window and the retry count are both bounded,
// and only reads are ever retried.
const STORAGE_KEY = 'launchpad_org_settling_until';
const SETTLING_WINDOW_MS = 20_000;
export const SETTLING_RETRY_DELAYS_MS = [500, 1000, 2000] as const;

export function markOrgSettling(now: number = Date.now()): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, String(now + SETTLING_WINDOW_MS));
  } catch {
    // Storage blocked: the dashboard just loads without the retry.
  }
}

export function isOrgSettling(now: number = Date.now()): boolean {
  try {
    const until = Number(sessionStorage.getItem(STORAGE_KEY));
    return Number.isFinite(until) && until > now;
  } catch {
    return false;
  }
}

export function settlingRetryDelay(attempt: number): number | null {
  return SETTLING_RETRY_DELAYS_MS[attempt] ?? null;
}
