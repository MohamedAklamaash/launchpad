import { sequelize } from "@/db/sequalize";

import { UserOTP } from "./models/invited-user-otp.model";
import { PasswordSettings } from "./models/password-settings.model";
import { RefreshToken } from "./models/refresh-token.model";
import { InvitedUser } from "./models/invited-user.model";
import { User } from "./models/user.model";

export const initModels = async () => {
    await sequelize.sync();
};

export {
    UserOTP,
    PasswordSettings,
    RefreshToken,
    InvitedUser,
    User,
}