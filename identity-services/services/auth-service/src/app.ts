import express, { type Express, Request, Response } from "express";
import cors from "cors";
import helmet from "helmet";
import { CreateInternalAuthMiddleware } from "@launchpad/common";
import { errorHandler } from "@/middleware/error.middleware";
import { registerRoutes, userRouter } from "@/router";
import { env } from "@/config/env";

export const createApp = (): Express => {
    const app = express()

    app.use(express.json());
    app.use(express.urlencoded({
        extended: true
    }))
    app.use(helmet());
    app.use(cors({
        origin: "*",
        credentials: true
    }));
    const routes = registerRoutes();

    app.use(CreateInternalAuthMiddleware(env.INTERNAL_API_TOKEN, {
        exemptPaths: [
            "/api/v1/liveness",
            "/api/v1/readiness",
            "/api/v1/healthz",
            "/api/v1/user/callback",
            "/api/v1/user/login",
            "/api/v1/user/me",
            "/api/user/callback",
            "/api/user/login",
            "/api/user/me",
            "/favicon.ico"
        ],
    }))

    app.use("/api/v1", routes);
    // Legacy alias to support GitHub redirect without v1 prefix
    app.use("/api/user", userRouter);
    app.use((_req: Request, res: Response) => {
        res.status(404).json({ message: 'Not Found' });
    });

    app.use(errorHandler)

    return app;
}