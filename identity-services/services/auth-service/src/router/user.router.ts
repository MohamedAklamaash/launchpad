import { Router } from "express";
import { LoginWithGitHub, GitHubCallback } from "@/controllers/user.controller";

export const userRouter: Router = Router();

userRouter.get("/login", LoginWithGitHub);
userRouter.get("/callback", GitHubCallback);
