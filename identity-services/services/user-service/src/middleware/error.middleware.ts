import { HttpError } from '@launchpad/common';

import type { ErrorRequestHandler } from 'express';
import { logger } from '@/utils/logger';

export const errorHandler: ErrorRequestHandler = (err, req, res, _next) => {
    logger.error({ err }, 'Unhandled error occurred');

    const error = err instanceof HttpError || Error() ? err : undefined;
    const statusCode = error?.statusCode ?? 500;
    const message =
        statusCode >= 500 ? 'Internal Server Error' : (error?.message ?? 'Unknown Error');
    const payload = error?.details ? { message, details: error.details } : { message };

    res.status(statusCode).json(payload);

    void _next();
};
