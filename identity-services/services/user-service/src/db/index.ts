import { sequelize } from "@/db/sequalize";

export const initModels = async () => {
    await sequelize.sync();
};

export * from "@/db/models/user.model"
export * from "@/db/sequalize"