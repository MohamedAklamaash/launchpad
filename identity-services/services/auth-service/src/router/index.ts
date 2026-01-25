import { Router, Request, Response } from "express";
import { connectToDatabase } from "@/db/sequalize";
import { authRouter } from "@/router/invited-user.routes";
import { userRouter } from "@/router/user.router";

export const registerRoutes = (): Router => {
    const app = Router();

    app.use("/auth", authRouter);
    app.use("/user", userRouter);

    app.get("/liveness", (_req: Request, res: Response) => {
        res.status(200).json({ status: "alive" });
    });

    app.get("/readiness", async (_req: Request, res: Response) => {
        try {
            await connectToDatabase();
            res.status(200).json({ status: "ready" });
        } catch (error) {
            res.status(503).json({
                status: "not ready",
                reason: "database unavailable",
            });
        }
    });

    app.get("/healthz", async (_req: Request, res: Response) => {
        try {
            await connectToDatabase();
            res.status(200).json({ status: "healthy" });
        } catch (error) {
            res.status(503).json({ status: "unhealthy" });
        }
    });

    return app;
}