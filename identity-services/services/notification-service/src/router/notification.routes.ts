import { Router } from "express";
import { GetUserNotifications } from "@/controllers/notification.controller";

export const notificationRouter: Router = Router();

notificationRouter.get("/user/:userId", GetUserNotifications);
