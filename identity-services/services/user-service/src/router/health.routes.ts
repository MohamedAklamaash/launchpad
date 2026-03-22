import { Router, type Request, type Response } from 'express';
import { sequelize } from '@/db/sequalize';
import {
    userCreatedPublisher,
    authConsumer,
    infraConsumer,
} from '@/messaging/consumer/user.consumer';
import { logger } from '@/utils/logger';

const router = Router();

/**
 * GET /liveness
 */
router.get('/liveness', (_req: Request, res: Response) => {
    res.status(200).json({ status: 'ok', service: 'user-service' });
});

/**
 * GET /readiness
 */
router.get('/readiness', async (_req: Request, res: Response) => {
    const checks: Record<string, { status: 'ok' | 'error'; detail?: string }> = {};
    let healthy = true;

    // Check Database
    try {
        await sequelize.authenticate();
        checks.database = { status: 'ok' };
    } catch (err: unknown) {
        checks.database = { status: 'error', detail: String(err) };
        healthy = false;
        logger.warn({ err }, 'Readiness: database check failed');
    }

    // Check RabbitMQ components
    checks.rabbitmq_publisher = {
        status: userCreatedPublisher.isConnected() ? 'ok' : 'error',
        detail: userCreatedPublisher.isConnected() ? undefined : 'publisher not connected',
    };
    if (!userCreatedPublisher.isConnected()) healthy = false;

    checks.rabbitmq_auth_consumer = {
        status: authConsumer.isConnected() ? 'ok' : 'error',
        detail: authConsumer.isConnected() ? undefined : 'auth consumer not connected',
    };

    checks.rabbitmq_infra_consumer = {
        status: infraConsumer.isConnected() ? 'ok' : 'error',
        detail: infraConsumer.isConnected() ? undefined : 'infra consumer not connected',
    };

    res.status(healthy ? 200 : 503).json({
        status: healthy ? 'ok' : 'degraded',
        service: 'user-service',
        checks,
        timestamp: new Date().toISOString(),
    });
});

/**
 * GET /healthz
 */
router.get('/healthz', (_req: Request, res: Response) => {
    res.status(200).json({ status: 'ok' });
});

export { router as healthRouter };
