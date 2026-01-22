import { NextFunction, Request, Response } from "express";
import { HttpError } from "../errors/http.error";
import { type RequestHandler } from "express"

const DEFAULT_HEADER_NAME = 'X-INTERNAL-TOKEN'

interface ReqOptions {
    exemptPaths?: string[],
    headerName?: string
}

export const CreateInternalAuthMiddleware = (expectedToken: string, options: ReqOptions): RequestHandler => {
    return (req: Request, _res: Response, next: NextFunction) => {
        const exemptPaths = new Set(options.exemptPaths ?? [])
        if (exemptPaths.has(req.path)) {
            next()
            return;
        }
        const headerName = options.headerName?.toLowerCase() ?? DEFAULT_HEADER_NAME;
        const providedToken = req.headers[headerName]
        const token = Array.isArray(providedToken) ? providedToken[0] : providedToken;
        if (typeof token !== "string" || expectedToken !== token) {
            next(new HttpError(401, "Unauthorized in Internal middleware", {
                "message": "Internal token mismatch with expected token"
            }));
            return;
        }
        next()
    }
}