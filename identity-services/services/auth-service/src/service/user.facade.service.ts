import axios from "axios";
import { User } from "@/db";
import { sequelize } from "@/db/sequalize";
import { env } from "@/config/env";
import { GithubCallbackInput, GithubUserUpsertInput } from "@/types/auth.user.types";
import { PublishUserRegistered } from "@/messaging/producer/user-created.message";

export class UserFacadeService {
    private clientId = env.GITHUB_CLIENT_ID;
    private clientSecret = env.GITHUB_CLIENT_SECRET;
    private redirectUri = env.GITHUB_REDIRECT_URI;

    public getAuthUrl(): string {
        return `https://github.com/login/oauth/authorize?client_id=${this.clientId}&redirect_uri=${this.redirectUri}&scope=repo%20read:org`;
    }

    public async handleCallback(input: GithubCallbackInput) {
        const { code } = input;
        const tokenRes = await axios.post(
            "https://github.com/login/oauth/access_token",
            {
                client_id: this.clientId,
                client_secret: this.clientSecret,
                code,
                redirect_uri: this.redirectUri,
            },
            { headers: { Accept: "application/json" } }
        );

        const token = tokenRes.data.access_token;
        if (!token) throw new Error("Failed to get GitHub token");

        const userRes = await axios.get("https://api.github.com/user", {
            headers: { Authorization: `Bearer ${token}` },
        });

        const { login: username, id: github_id, avatar_url, email } = userRes.data;
        const githubIdStr = String(github_id);

        return { token, username, github_id: githubIdStr, avatar_url, email };
    }

    public async upsertUser(githubData: GithubUserUpsertInput) {
        return sequelize.transaction(async (transaction) => {
            let user = await User.findOne({ where: { github_id: githubData.github_id }, transaction });

            if (!user && githubData.email) {
                user = await User.findOne({ where: { email: githubData.email }, transaction });
            }

            if (user) {
                user.github_id = githubData.github_id;
                user.github_token = githubData.token;
                user.profile_url = githubData.avatar_url;
                await user.save({ transaction });
            } else {
                user = await User.create({
                    user_name: githubData.username,
                    github_id: githubData.github_id,
                    github_token: githubData.token,
                    profile_url: githubData.avatar_url,
                    email: githubData.email ?? `${githubData.github_id}@github.com`,
                    role: "admin",
                    
                }, { transaction });

                PublishUserRegistered({
                    id: user.id,
                    email: user.email,
                    user_name: user.user_name,
                    created_at: user.created_at, // Access via property, model should map it
                    infra_id: [], // GitHub users default to empty infra?
                    role: user.role,
                    updated_at: user.updated_at,
                    metadata: user.metadata || {}
                });
            }

            return user;
        });
    }
}
