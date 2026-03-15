import express, { type Express, Request, Response } from "express";
import cors from "cors";
import helmet from "helmet";
import swaggerUi from "swagger-ui-express";
import swaggerJsdoc from "swagger-jsdoc";
import { CreateInternalAuthMiddleware } from "@launchpad/common";
import { errorHandler } from "@/middleware/error.middleware";
import { registerRoutes, userRouter } from "@/router";
import { env } from "@/config/env";

const swaggerSpec = swaggerJsdoc({
    definition: {
        openapi: "3.0.0",
        info: { title: "Auth Service API", version: "1.0.0" },
        components: {
            securitySchemes: {
                bearerAuth: { type: "http", scheme: "bearer", bearerFormat: "JWT" },
            },
        },
        security: [{ bearerAuth: [] }],
    },
    apis: ["./src/router/*.ts"],
});

export const createApp = (): Express => {
    const app = express()

    app.use(express.json());
    app.use(express.urlencoded({ extended: true }))
    app.use(helmet());
    app.use(cors({ origin: "*", credentials: true }));

    app.use("/api/v1/docs", swaggerUi.serve, swaggerUi.setup(swaggerSpec));
    app.get("/api/v1/docs.json", (_req, res) => res.json(swaggerSpec));

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
            "/api/v1/docs",
            "/api/v1/docs.json",
            "/favicon.ico"
        ],
    }))

    app.use("/api/v1", routes);
    app.use("/api/user", userRouter);
    app.use((_req: Request, res: Response) => {
        res.status(404).json({ message: 'Not Found' });
    });

    app.use(errorHandler)

    return app;
}