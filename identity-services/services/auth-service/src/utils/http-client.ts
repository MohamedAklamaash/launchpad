import { ResilientHttpClient } from "@launchpad/common";
import { env } from "@/config/env";

export const githubHttpClient = new ResilientHttpClient({
    name: "github-oauth",
    requestTimeout: env.HTTP_REQUEST_TIMEOUT_MS,
    circuitBreaker: {
        failureThreshold: env.CB_FAILURE_THRESHOLD,
        timeout: env.CB_TIMEOUT_MS,
        successThreshold: env.CB_SUCCESS_THRESHOLD,
        fallback: () => {
            throw new Error("GitHub OAuth is temporarily unavailable. Please try again later.");
        },
    },
});
