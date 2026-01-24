import express, { type Express, Request, Response } from "express";
import cors from "cors";
import helmet from "helmet";
import { errorHandler } from "@/middleware/error.middleware";
import { createRoutes } from "@/router";

export const createApp = (): Express => {
    const app = express()

    app.use(express.json());
    app.use(helmet());
    app.use(cors({
        origin: "*",
        credentials: true
    }));

    const routes = createRoutes();
    app.use("/api", routes);

    app.use((_req: Request, res: Response) => {
        res.status(404).json({ message: 'Not Found' });
    });

    app.use(errorHandler)

    return app;
}