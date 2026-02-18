import { Router } from "express";
import { userRouter } from "./user.routes";
import { healthRouter } from "./health.routes";

export const createRoutes = (): Router => {
    const router = Router();

    // Health probes (exempt from internal auth middleware if applicable)
    router.use("/", healthRouter);

    router.use("/users", userRouter);

    return router;
}