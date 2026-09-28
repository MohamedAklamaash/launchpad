import { NextFunction, Request, Response } from 'express';
import { notificationService } from '@/service/notification.service';
import { HttpError } from '@launchpad/common';
import { resolveCaller } from '@/utils/resolve-caller';

// No path parameter for the target user — the caller can only ever list their own
// notifications, derived from the verified access token. Nothing in the frontend or
// other services called the old `/user/:userId` shape (verified against the codebase),
// so there was no caller to preserve compatibility for.
export const GetMyNotifications = async (req: Request, res: Response, next: NextFunction) => {
    try {
        const caller = resolveCaller(req);
        const notifications = await notificationService.getByUser(caller.sub);
        res.status(200).json(notifications);
    } catch (error: unknown) {
        if (error instanceof HttpError) {
            next(error);
            return;
        }
        console.error('Error fetching notifications', error);
        next(new HttpError(500, 'Internal Server Error'));
    }
};
