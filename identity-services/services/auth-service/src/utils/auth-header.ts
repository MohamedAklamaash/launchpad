import { Request } from "express";
import { HttpError } from "@launchpad/common";

export const getAuthHeader = (req: Request) => {
    const authHeader = req.headers.authorization;
    if (!authHeader) {
        throw new HttpError(401, "Authorization header is missing");
    }
    const token = authHeader.split(" ")[1];
    return token;
};
