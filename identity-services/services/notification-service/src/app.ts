import express, { type Express, Request, Response } from "express";
import cors from "cors";
import helmet from "helmet";
import { errorHandler } from "@/middleware/error.middleware";

export const createApp = (): Express => {
    const app = express()

    app.use(express.json());
    app.use(helmet());
    app.use(cors({
        origin: "*",
        credentials: true
    }));

    app.use((_req: Request, res: Response) => {
        res.status(404).json({ message: 'Not Found' });
    });

    app.use(errorHandler)

    return app;
}