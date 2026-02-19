import { BaseService } from "@/service/invited-users/invited-user.base.service";
import { InvitedUser } from "@/db";
import { sequelize } from "@/db/sequalize";
import { hashPassword } from "@/utils/handle-password";
import { HttpError } from "@launchpad/common";
import { InvitedUserRegisterInput, USER_ROLE } from "@/types/auth.invited_user.types";
import { PublishUserRegistered, userAuthenticationQueue } from "@/messaging/producer/user-created.message";
import { AUTHENTICATE_INVITED_USER_EVENT } from "@launchpad/common";

export class InvitedUserService extends BaseService {

    public async register(input: InvitedUserRegisterInput, super_user: string) {
        const { email, password, user_name, infra_id, role } = input;
        return sequelize.transaction(async (transaction) => {
            const existingUser = await InvitedUser.findOne({ where: { email }, transaction });
            const existingUserName = await InvitedUser.findOne({ where: { user_name }, transaction });
            if (existingUserName && !existingUser) {
                throw new HttpError(409, "Username already taken");
            }
            let user: InvitedUser;

            if (existingUser) {
                if (!existingUser.infra_id.includes(infra_id)) {
                    existingUser.infra_id = [...existingUser.infra_id, infra_id];
                    existingUser.is_authenticated = false;
                    user = await existingUser.save({ transaction });
                } else {
                    if (!existingUser.is_authenticated) {
                        user = existingUser; // User exists but not authenticated, proceed to resend OTP
                    } else {
                        throw new HttpError(409, "User already registered and authenticated in this infra");
                    }
                }
            } else {
                const passwordHash = await hashPassword(password);
                user = await InvitedUser.create({
                    email,
                    user_name: user_name,
                    infra_id: [infra_id],
                    password_hash: passwordHash,
                    role: role as USER_ROLE,
                    is_authenticated: false,
                    invited_by: super_user
                }, { transaction });
            }
            const otpRecord = await this.createOTP(user.id, infra_id, transaction);
            // publish notify event here
            await userAuthenticationQueue.add(AUTHENTICATE_INVITED_USER_EVENT, {
                user_id: user.id,
                email,
                otp: otpRecord.otp,
                infra_id,
                source: "mail",
                user_name,
            });

            try {
                PublishUserRegistered({
                    id: user.id,
                    email,
                    user_name,
                    created_at: user.created_at,
                    infra_id: user.infra_id,
                    role,
                    updated_at: user.updated_at,
                    metadata: {},
                    invited_by: super_user
                });
            } catch (pubError) {
                console.error("Failed to publish user registered event", pubError);
                // Do not fail transaction if publishing fails, or maybe we should? best effort for now.
            }

            return { user, otp: otpRecord.otp };
        });
    }
}
