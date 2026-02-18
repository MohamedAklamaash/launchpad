import express, { type Express, Request, Response } from "express";
import cors from "cors";
import helmet from "helmet";
import { CreateInternalAuthMiddleware } from "@launchpad/common";
import { errorHandler } from "@/middleware/error.middleware";
import { createRoutes } from "@/router";
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

    app.use(CreateInternalAuthMiddleware(env.INTERNAL_API_TOKEN, {
        exemptPaths: ["/api/v1/liveness", "/api/v1/readiness", "/api/v1/healthz", "/favicon.ico"],
    }))

    const routes = createRoutes();
    app.use("/api/v1", routes);

    app.use((_req: Request, res: Response) => {
        res.status(404).json({ message: 'Not Found' });
    });

    app.use(errorHandler)

    return app;
}