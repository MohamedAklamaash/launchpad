import { Router, type Request, type Response } from 'express';
import { sequelize } from '@/db/sequalize';
import { userCreatedPublisher } from '@/messaging/producer/user-created.message';
import { infraCreatedConsumer } from '@/messaging/consumer/infra-created.consumer';
import { logger } from '@/utils/logger';

const router: Router = Router();

router.get('/liveness', (_req: Request, res: Response) => {
    res.status(200).json({ status: 'ok', service: 'auth-service' });
});

router.get('/readiness', async (_req: Request, res: Response) => {
    const checks: Record<string, { status: 'ok' | 'error'; detail?: string }> = {};
    let healthy = true;

    // Check PostgreSQL
    try {
        await sequelize.authenticate();
        checks.database = { status: 'ok' };
    } catch (err: unknown) {
        checks.database = { status: 'error', detail: String(err) };
        healthy = false;
        logger.warn({ err }, 'Readiness: database check failed');
    }

    // Check RabbitMQ publisher
    checks.rabbitmq_publisher = {
        status: userCreatedPublisher.isConnected() ? 'ok' : 'error',
        detail: userCreatedPublisher.isConnected() ? undefined : 'publisher not connected',
    };
    if (!userCreatedPublisher.isConnected()) healthy = false;

    // Check RabbitMQ consumer
    checks.rabbitmq_consumer = {
        status: infraCreatedConsumer.isConnected() ? 'ok' : 'error',
        detail: infraCreatedConsumer.isConnected() ? undefined : 'consumer not connected',
    };

    res.status(healthy ? 200 : 503).json({
        status: healthy ? 'ok' : 'degraded',
        service: 'auth-service',
        checks,
        timestamp: new Date().toISOString(),
    });
});

router.get('/healthz', (_req: Request, res: Response) => {
    res.status(200).json({ status: 'ok' });
});

export { router as healthRouter };
