import { Request, Response } from "express";
import { notificationService } from "@/service/notification.service";
import { HttpError } from "@launchpad/common";

export const GetUserNotifications = async (req: Request, res: Response) => {
    try {
        const userId = req.params.userId as string;
        if (!userId) throw new HttpError(400, "User ID is required");

        const notifications = await notificationService.getByUser(userId);

        return res.status(200).json(notifications);
    } catch (error: any) {
        if (error instanceof HttpError) throw error;
        console.error("Error fetching notifications", error);
        throw new HttpError(500, "Internal Server Error");
    }
};
