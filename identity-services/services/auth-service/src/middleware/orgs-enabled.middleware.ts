import type { NextFunction, Request, Response } from 'express';
import { env } from '@/config/env';

// With ORGS_ENABLED off these routes must be indistinguishable from a path that was never
// registered, including to an unauthenticated probe: this answers with the exact body of
// the app's catch-all 404, before any token parsing can produce a 401/422 that would
// reveal the route exists.
export const requireOrgsEnabled = (_req: Request, res: Response, next: NextFunction) => {
    if (!env.ORGS_ENABLED) {
        res.status(404).json({ message: 'Not Found' });
        return;
    }
    next();
};
