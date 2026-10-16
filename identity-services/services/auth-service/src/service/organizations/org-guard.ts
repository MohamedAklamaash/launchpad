import { HttpError } from '@launchpad/common';
import { env } from '@/config/env';

// Every mutating function in src/service/organizations/ calls this first. There is no HTTP
// surface in F9 ticket 2 (plan/F9-teams-and-orgs.md, Execution plan item 2) and nothing in
// the request path calls these yet, but the guard still exists so a future caller can never
// engage this code with the flag off — "no request behaviour change" stays true by
// construction, not by convention.
export const assertOrgsEnabled = (): void => {
    if (!env.ORGS_ENABLED) {
        throw new HttpError(404, 'Organizations are not enabled');
    }
};
