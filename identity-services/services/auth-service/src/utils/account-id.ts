import { v5 as uuidv5 } from 'uuid';

// Fixed, checked-in constant — never an env var. F9's Account backfill (and any future
// service that needs to agree on the same id without a cross-service call) must compute
// identically every time it runs, in every environment; an env-configurable namespace could
// drift between services/environments and silently break that agreement.
export const ACCOUNT_NAMESPACE = 'fedc231c-616e-4c0c-a2a8-bfb9491b9bd1';

// Email is mutable and not unique across the two credential tables, but it's the only
// identity signal a legacy users/invited_users row carries. Normalizing to lowercase+trim
// (no plus-addressing or dot-stripping — that would invent identity semantics the rest of
// the platform doesn't have) and hashing it into a uuid5 makes the resulting Account id
// deterministic and idempotent to recompute, and deliberately shared whenever two rows
// (in the same table or across tables) normalize to the same email — see F9 ticket 1 in
// plan/F9-teams-and-orgs.md for why this is "documented, not fixed" rather than avoided.
export const normalizeEmail = (email: string): string => email.trim().toLowerCase();

export const accountIdForEmail = (email: string): string =>
    uuidv5(normalizeEmail(email), ACCOUNT_NAMESPACE);
