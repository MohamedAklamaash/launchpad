import { NextFunction, Request, Response } from "express";
import { ZodError, ZodTypeAny } from "zod";
import { HttpError } from "../errors/http.error.js";

type Schema = ZodTypeAny;
type ParamsRecord = Record<string, string>;
type QueryRecord = Record<string, unknown>;

export interface RequestValidationSchemas {
    body?: Schema;
    params?: Schema;
    query?: Schema;
}

const formattedError = (error: ZodError) =>
    error.issues.map((issue) => ({
        path: issue.path.join("."),
        message: issue.message,
    }));

export const validateRequest = (schemas: RequestValidationSchemas) =>
    (req: Request, _res: Response, next: NextFunction) => {
        try {
            if (schemas.body) {
                req.body = schemas.body.parse(req.body);
            }

            if (schemas.params) {
                const paramsRecord = schemas.params.parse(req.params) as ParamsRecord;
                req.params = paramsRecord as Request["params"];
            }

            if (schemas.query) {
                const parsedQuery = schemas.query.parse(req.query) as QueryRecord;
                Object.assign(req.query, parsedQuery);
            }

            next();
        } catch (error) {
            if (error instanceof ZodError) {
                return next(
                    new HttpError(422, "Validation Error", {
                        issues: formattedError(error),
                    })
                );
            }
            next(error);
        }
    };