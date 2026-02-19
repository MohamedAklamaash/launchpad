import { Router, type Request, type Response } from "express";
import { ConnectMongoDB } from "@/db/client/mongo.client";
import { userEventsWorker } from "@/messaging/consumer/user-event.consumer";
import { logger } from "@/utils/logger";

const router: Router = Router();

router.get("/liveness", (_req: Request, res: Response) => {
    res.status(200).json({ status: "ok", service: "notification-service" });
});

router.get("/readiness", async (_req: Request, res: Response) => {
    const checks: Record<string, { status: "ok" | "error"; detail?: string }> = {};
    let healthy = true;

    // Check MongoDB
    try {
        const client = await ConnectMongoDB();
        await client?.db().admin().ping();
        checks.mongodb = { status: "ok" };
    } catch (err: unknown) {
        checks.mongodb = { status: "error", detail: String(err) };
        healthy = false;
        logger.warn({ err }, "Readiness: mongodb check failed");
    }

    // Check BullMQ / Redis
    const redisStatus = userEventsWorker.isRunning() ? "ok" : "error";
    checks.redis_worker = {
        status: redisStatus,
        detail: redisStatus === "ok" ? undefined : "worker not running",
    };
    if (redisStatus === "error") healthy = false;

    res.status(healthy ? 200 : 503).json({
        status: healthy ? "ok" : "degraded",
        service: "notification-service",
        checks,
        timestamp: new Date().toISOString(),
    });
});

router.get("/healthz", (_req: Request, res: Response) => {
    res.status(200).json({ status: "ok" });
});

export { router as healthRouter };
