// Handling of the organization invitation token on the accept page.
//
// The token arrives in the URL fragment (#token=...), so it is never sent to a server or in
// a Referer header. It is read once, scrubbed from the address bar and history straight
// away, and kept only in sessionStorage (per tab, gone when the tab closes) so it survives
// the trip through login. It is never put in a query string, a `next` value, or
// localStorage.

export const PENDING_INVITE_KEY = 'launchpad.pending_invite_token';
export const ACCEPT_PATH = '/invitations/accept';

// 32 random bytes, base64url, unpadded. Same shape the backend accepts.
const TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;

export function tokenFromHash(hash: string): string | null {
  const value = new URLSearchParams(hash.replace(/^#/, '')).get('token');
  return value && TOKEN_RE.test(value) ? value : null;
}

interface FragmentWindow {
  location: { hash: string; pathname: string };
  history: { replaceState: (data: unknown, unused: string, url: string) => void };
}

// Reads the token and removes the whole fragment (and any query string) from the visible
// URL in the same synchronous step.
export function takeTokenFromUrl(win: FragmentWindow): string | null {
  const token = tokenFromHash(win.location.hash);
  win.history.replaceState(null, '', win.location.pathname);
  return token;
}

export function stashInviteToken(storage: Pick<Storage, 'setItem'>, token: string): void {
  storage.setItem(PENDING_INVITE_KEY, token);
}

export function readStashedInviteToken(storage: Pick<Storage, 'getItem'>): string | null {
  const value = storage.getItem(PENDING_INVITE_KEY);
  return value && TOKEN_RE.test(value) ? value : null;
}

export function clearInviteToken(storage: Pick<Storage, 'removeItem'>): void {
  storage.removeItem(PENDING_INVITE_KEY);
}

export function hasPendingInviteToken(storage: Pick<Storage, 'getItem'>): boolean {
  return readStashedInviteToken(storage) !== null;
}
