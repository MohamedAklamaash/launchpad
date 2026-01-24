import { NextFunction, Request, Response } from 'express';
import { userService } from '@/service/user.service';
import { HttpError } from '@launchpad/common';

export const GetUserById = async (req: Request, res: Response, next: NextFunction) => {
    try {
        const userId = req.params.userId as string;
        if (!userId) {
            throw new HttpError(400, "User ID is required");
        }
        const user = await userService.getUserById(userId);
        res.status(200).json(user);
    } catch (error) {
        next(error);
    }
};

export const SearchUsers = async (req: Request, res: Response, next: NextFunction) => {
    try {
        const query = req.query.q as string;
        if (!query) {
            throw new HttpError(400, "Search query 'q' is required");
        }
        const users = await userService.searchUsers({ query });
        res.status(200).json(users);
    } catch (error) {
        next(error);
    }
};
