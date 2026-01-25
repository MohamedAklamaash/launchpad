import { Router, Request, Response } from "express";
import { userRouter } from "./user.routes";
import { ConnectToDatabase } from "@/db";

export const createRoutes = (): Router => {
    const router = Router();
    router.use("/users", userRouter);

    router.get("/liveness", (_req: Request, res: Response) => {
        res.status(200).json({ status: "alive" });
    });

    router.get("/readiness", async (_req: Request, res: Response) => {
        try {
            await ConnectToDatabase();
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
            await ConnectToDatabase();
            res.status(200).json({ status: "healthy" });
        } catch (error) {
            res.status(503).json({ status: "unhealthy" });
        }
    });

    return router;
}