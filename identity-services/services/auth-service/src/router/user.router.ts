import { Router } from "express";
import { LoginWithGitHub, GitHubCallback, GetCurrentUser } from "@/controllers/user.controller";

export const userRouter: Router = Router();

userRouter.get("/login", LoginWithGitHub);
userRouter.get("/callback", GitHubCallback);
userRouter.get("/me", GetCurrentUser);
