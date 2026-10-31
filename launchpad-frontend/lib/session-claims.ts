// Reads which org the session is scoped to, and which Account it belongs to, out of the
// access token payload. These are selectors for the UI only. The role is never read from
// the token: it comes from GET /organizations, and the backend re-derives it per request.
export interface SessionClaims {
  orgId?: string;
  accountId?: string;
}

export function parseSessionClaims(token: string | null | undefined): SessionClaims {
  if (!token) return {};
  try {
    const payload = token.split('.')[1];
    if (!payload) return {};
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/');
    const bytes = Uint8Array.from(atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, '=')), (c) =>
      c.charCodeAt(0),
    );
    const claims = JSON.parse(new TextDecoder().decode(bytes)) as { org_id?: unknown; account_id?: unknown };
    return {
      orgId: typeof claims.org_id === 'string' ? claims.org_id : undefined,
      accountId: typeof claims.account_id === 'string' ? claims.account_id : undefined,
    };
  } catch {
    return {};
  }
}

export function currentSessionClaims(): SessionClaims {
  if (typeof window === 'undefined') return {};
  return parseSessionClaims(localStorage.getItem('access_token'));
}
