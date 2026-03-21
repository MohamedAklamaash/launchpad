import { Router } from 'express';
import { notificationRouter } from '@/router/notification.routes';
import { healthRouter } from './health.routes';

export const createRoutes = (): Router => {
    const router: Router = Router();

    router.use('/', healthRouter);
    router.use('/notifications', notificationRouter);

    return router;
};
