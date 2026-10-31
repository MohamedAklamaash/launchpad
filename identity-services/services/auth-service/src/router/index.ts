import { Router } from 'express';
import { authRouter } from '@/router/invited-user.routes';
import { userRouter } from '@/router/user.router';
import { invitationRouter, organizationRouter } from '@/router/organization.routes';
export { userRouter };
import { healthRouter } from '@/router/health.routes';

export const registerRoutes = (): Router => {
    const app = Router();

    app.use('/', healthRouter);

    app.use('/auth', authRouter);
    app.use('/user', userRouter);
    app.use('/organizations', organizationRouter);
    app.use('/invitations', invitationRouter);

    return app;
};
