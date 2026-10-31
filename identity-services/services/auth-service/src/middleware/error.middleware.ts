import { HttpError } from '@launchpad/common';
import type { ErrorRequestHandler } from 'express';
import { logger } from '@/utils/logger';
import { ReauthRequiredError } from '@/utils/reauth';

export const errorHandler: ErrorRequestHandler = (err, req, res, _next) => {
    const statusCode = err instanceof HttpError ? err.statusCode : 500;
    const message = err.message || 'Internal Server Error';

    if (statusCode >= 500) {
        logger.error({ err: err.message, stack: err.stack }, 'Unhandled error occurred');
    } else {
        logger.warn({ err: err.message }, 'Request error occurred');
    }

    const responseMessage = statusCode >= 500 ? 'Internal Server Error' : message;

    // The dashboard's session-revoke interceptor keys on a top-level `code`.
    let payload: Record<string, unknown> = { message: responseMessage };
    if (err instanceof ReauthRequiredError) {
        payload = { message: responseMessage, code: err.code };
    } else if (err instanceof HttpError && err.details) {
        payload = { message: responseMessage, details: err.details };
    }

    res.status(statusCode).json(payload);
};
