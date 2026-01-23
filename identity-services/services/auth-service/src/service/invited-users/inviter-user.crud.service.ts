import { BaseService } from "@/service/invited-users/invited-user.base.service";
import { InvitedUser } from "@/db";
import { sequelize } from "@/db/sequalize";
import { hashPassword } from "@/utils/handle-password";
import { HttpError } from "@launchpad/common";

export class InvitedUserService extends BaseService {

    public async register(email: string, password: string, userName: string, infraId: string, role: string) {
        return sequelize.transaction(async (transaction) => {
            const existingUser = await InvitedUser.findOne({ where: { email }, transaction });
            let user: InvitedUser;

            if (existingUser) {
                if (!existingUser.infra_id.includes(infraId)) {
                    existingUser.infra_id.push(infraId);
                    existingUser.is_authenticated = false;
                    user = await existingUser.save({ transaction });
                } else {
                    throw new HttpError(400, "User already exists in this infra");
                }
            } else {
                const passwordHash = await hashPassword(password);
                user = await InvitedUser.create({
                    email,
                    user_name: userName,
                    infra_id: [infraId],
                    password_hash: passwordHash,
                    role: role as any,
                    is_authenticated: false,
                }, { transaction });
            }

            return user;
        });
    }

    public async getUserById(userId: string) {
        const user = await InvitedUser.findByPk(userId);
        if (!user) throw new HttpError(404, "User not found");
        return user;
    }

    public async deleteUser(userId: string) {
        await InvitedUser.destroy({ where: { id: userId } });
    }
}
