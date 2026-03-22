import { NextFunction, Request, Response } from 'express';
import { HttpError } from '../errors/http.error.js';
import { type RequestHandler } from 'express';

const DEFAULT_HEADER_NAME = 'X-INTERNAL-TOKEN';

interface ReqOptions {
    exemptPaths?: string[];
    headerName?: string;
}

export const CreateInternalAuthMiddleware = (
    expectedToken: string,
    options: ReqOptions,
): RequestHandler => {
    return (req: Request, _res: Response, next: NextFunction) => {
        const exemptPaths = options.exemptPaths ?? [];
        const currentPath = req.path.endsWith('/') ? req.path.slice(0, -1) : req.path;
        const isExempt = exemptPaths.some((path) => {
            const pattern = path.endsWith('/') ? path.slice(0, -1) : path;
            return currentPath === pattern || currentPath.endsWith(pattern);
        });

        if (isExempt) {
            next();
            return;
        }

        const headerName = options.headerName?.toLowerCase() ?? DEFAULT_HEADER_NAME.toLowerCase();
        const providedToken = req.headers[headerName];
        const token = Array.isArray(providedToken) ? providedToken[0] : providedToken;

        if (typeof token !== 'string' || expectedToken !== token) {
            console.error(`[InternalAuth] Mismatch on path: ${req.path}`);
            console.error(`[InternalAuth] Provided: ${token ? 'present' : 'missing'}`);
            if (token && expectedToken) {
                console.error(
                    `[InternalAuth] Lengths - Provided: ${token.length}, Expected: ${expectedToken.length}`,
                );
            }

            next(
                new HttpError(401, 'Unauthorized in Internal middleware', {
                    message: 'Internal token mismatch with expected token',
                }),
            );
            return;
        }
        next();
    };
};
