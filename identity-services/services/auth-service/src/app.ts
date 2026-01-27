import express, { type Express, Request, Response } from "express";
import cors from "cors";
import helmet from "helmet";
import { CreateInternalAuthMiddleware } from "@launchpad/common";
import { errorHandler } from "@/middleware/error.middleware";
import { registerRoutes } from "@/router";
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
        exemptPaths: ["/liveness", "/readiness", "/healthz", "/user/callback", "/favicon.ico"],
    }))

    app.use("/api", routes);
    app.use((_req: Request, res: Response) => {
        res.status(404).json({ message: 'Not Found' });
    });

    app.use(errorHandler)

    return app;
}