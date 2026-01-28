import { User } from "@/db";
import { AccessTokenPayload } from "./handle-token";
import { USER_ROLE } from "@/types/auth.invited_user.types";

export const superAdminMiddleware = async (payload: AccessTokenPayload) => {
    if (payload.role !== USER_ROLE.SUPER_ADMIN) {
        throw new Error("Unauthorized");
    }

    const user = await User.findOne({ where: { id: payload.sub } });
    if (!user) {
        throw new Error("User not found");
    }

    return user;
}