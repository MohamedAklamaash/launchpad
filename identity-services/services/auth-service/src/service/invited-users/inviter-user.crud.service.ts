import { BaseService } from "@/service/invited-users/invited-user.base.service";
import { InvitedUser } from "@/db";
import { sequelize } from "@/db/sequalize";
import { hashPassword } from "@/utils/handle-password";
import { HttpError } from "@launchpad/common";
import { InvitedUserRegisterInput } from "@/types/auth.invited_user.types";
import { userAuthenticationQueue } from "@/messaging/producer/user-created.message";
import { AUTHENTICATE_INVITED_USER_EVENT } from "@launchpad/common";

export class InvitedUserService extends BaseService {

    public async register(input: InvitedUserRegisterInput) {
        const { email, password, user_name, infra_id, role } = input;
        return sequelize.transaction(async (transaction) => {
            const existingUser = await InvitedUser.findOne({ where: { email }, transaction });
            let user: InvitedUser;

            if (existingUser) {
                if (!existingUser.infra_id.includes(infra_id)) {
                    existingUser.infra_id.push(infra_id);
                    existingUser.is_authenticated = false;
                    user = await existingUser.save({ transaction });
                } else {
                    throw new HttpError(400, "User already exists in this infra");
                }
            } else {
                const passwordHash = await hashPassword(password);
                user = await InvitedUser.create({
                    email,
                    user_name: user_name,
                    infra_id: [infra_id],
                    password_hash: passwordHash,
                    role: role as any,
                    is_authenticated: false,
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

            return user;
        });
    }
}
