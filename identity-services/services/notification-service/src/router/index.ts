import { Router, Request, Response } from "express";
import { notificationRouter } from "@/router/notification.routes";
import { ConnectMongoDB } from "@/db/client/mongo.client";

export const createRoutes = (): Router => {
    const router: Router = Router();

    router.use("/notifications", notificationRouter);

    router.get("/liveness", (_req: Request, res: Response) => {
        res.status(200).json({ status: "alive" });
    });

    router.get("/readiness", async (_req: Request, res: Response) => {
        try {
            await ConnectMongoDB();
            res.status(200).json({ status: "ready" });
        } catch (error) {
            res.status(503).json({
                status: "not ready",
                reason: "database unavailable",
            });
        }
    });

    router.get("/healthz", async (_req: Request, res: Response) => {
        try {
            await ConnectMongoDB();
            res.status(200).json({ status: "healthy" });
        } catch (error) {
            res.status(503).json({ status: "unhealthy" });
        }
    });

    return router;
}