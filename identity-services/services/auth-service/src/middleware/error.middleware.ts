import { HttpError } from "@launchpad/common";
import type { ErrorRequestHandler } from "express";
import { logger } from "@/utils/logger";

export const errorHandler: ErrorRequestHandler = (err, req, res, _next) => {
    const statusCode = err instanceof HttpError ? err.statusCode : 500;
    const message = err.message || "Internal Server Error";

    if (statusCode >= 500) {
        logger.error({ err: err.message, stack: err.stack }, "Unhandled error occurred");
    } else {
        logger.warn({ err: err.message }, "Request error occurred");
    }

    const responseMessage = statusCode >= 500 ? "Internal Server Error" : message;

    const payload = (err instanceof HttpError && err.details)
        ? { message: responseMessage, details: err.details }
        : { message: responseMessage };

    res.status(statusCode).json(payload);
};