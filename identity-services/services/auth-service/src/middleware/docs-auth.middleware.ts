import { Request, Response, NextFunction } from 'express';
import { verifyAccessToken } from '@/utils/handle-token';
import { User } from '@/db';

export const docsAuth = async (req: Request, res: Response, next: NextFunction) => {
    const auth = req.headers.authorization;
    if (!auth?.startsWith('Bearer ')) {
        res.status(401).send('Authorization required to view docs');
        return;
    }
    try {
        const payload = verifyAccessToken(auth.split(' ')[1]);
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
