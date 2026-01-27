import { Request, Response } from "express";
import { UserFacadeService } from "@/service/user.facade.service";

const userService = new UserFacadeService();

export const LoginWithGitHub = async (_req: Request, res: Response) => {
    const url = userService.getAuthUrl();
    return res.redirect(url);
};

export const GitHubCallback = async (req: Request, res: Response) => {
    const code = req.query.code as string;
    if (!code) return res.status(400).json({ message: "Missing code" });

    try {
        const githubData = await userService.handleCallback({ code });
        const authResponse = await userService.upsertUser(githubData);

        return res.status(200).json({
            message: "GitHub login successful",
            ...authResponse
        });
    } catch (err: any) {
        return res.status(500).json({ message: err.message });
    }
};
