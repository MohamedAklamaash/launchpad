import { Request, Response, NextFunction } from 'express';
import { verifySessionToken } from '@/utils/handle-token';
import { User } from '@/db';

export const docsAuth = async (req: Request, res: Response, next: NextFunction) => {
    const auth = req.headers.authorization;
    if (!auth?.startsWith('Bearer ')) {
        res.status(401).send('Authorization required to view docs');
        return;
    }
    try {
        // verifySessionToken (not the raw verifyAccessToken): a narrow-purpose token —
        // currently PasswordService's 5-minute password_reset token — proves someone
        // read one inbox, not that they should see internal API docs.
        const payload = verifySessionToken(auth.split(' ')[1]);
        const user = await User.findOne({ where: { user_name: payload.user_name } });
        if (!user) {
            res.status(403).send('User not found');
            return;
        }
        next();
    } catch {
        res.status(401).send('Invalid token');
    }
};
