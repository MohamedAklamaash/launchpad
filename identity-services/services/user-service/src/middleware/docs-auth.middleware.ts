import { Request, Response, NextFunction } from 'express';
import { HttpError } from '@launchpad/common';
import { resolveCaller } from '@/utils/resolve-caller';
import { User } from '@/db';

export const docsAuth = async (req: Request, res: Response, next: NextFunction) => {
    try {
        // resolveCaller (not a bare jwt.verify): pins the algorithm and rejects a
        // narrow-purpose token — currently auth-service's 5-minute password_reset
        // token — the same way every data route in this service now does. Proving you
        // read one inbox shouldn't also grant access to internal API docs.
        const payload = resolveCaller(req);
        const user = await User.findOne({ where: { user_name: payload.user_name } });
        if (!user) {
            res.status(403).send('User not found');
            return;
        }
        next();
    } catch (error) {
        const status = error instanceof HttpError ? error.statusCode : 401;
        res.status(status).send('Invalid token');
    }
};
