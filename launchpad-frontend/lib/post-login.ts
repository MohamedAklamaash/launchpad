import { ACCEPT_PATH, hasPendingInviteToken } from '@/lib/invite-token';

const NEXT_KEY = 'launchpad.post_login_next';

// `next` is a plain in-app path: letters, digits, slash, dash, underscore. No scheme, host,
// query or fragment, so it can neither leave the site nor carry a secret.
const SAFE_NEXT_RE = /^\/(?!\/)[A-Za-z0-9/_-]*$/;

export function safeNextPath(value: string | null | undefined): string | null {
  return value && SAFE_NEXT_RE.test(value) ? value : null;
}

// GitHub login leaves the site and comes back through /auth/callback, so a `next` from the
// login URL is parked here for the callback to pick up.
export function rememberNext(next: string | null): void {
  try {
    if (next) sessionStorage.setItem(NEXT_KEY, next);
  } catch {
    // sessionStorage unavailable: fall back to /dashboard.
  }
}

// Where to go once a session exists. A pending invitation always wins, then a remembered
// or explicit `next`, then the dashboard.
export function postLoginPath(explicitNext?: string | null): string {
  try {
    if (hasPendingInviteToken(sessionStorage)) return ACCEPT_PATH;
    const stored = safeNextPath(sessionStorage.getItem(NEXT_KEY));
    sessionStorage.removeItem(NEXT_KEY);
    return safeNextPath(explicitNext) ?? stored ?? '/dashboard';
  } catch {
    return safeNextPath(explicitNext) ?? '/dashboard';
  }
}

export function loginUrlWithNext(path: string): string {
  const next = safeNextPath(path);
  return next ? `/login?next=${encodeURIComponent(next)}` : '/login';
}
