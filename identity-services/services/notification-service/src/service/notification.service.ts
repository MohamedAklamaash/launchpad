import { Collection } from "mongodb";
import { ConnectMongoDB, INotification } from "@/db";
import { logger } from "@/utils/logger";

export class NotificationService {
    private collection?: Collection<INotification>;

    private async getCollection(): Promise<Collection<INotification>> {
        if (this.collection) return this.collection;

        const client = await ConnectMongoDB();
        if (!client) {
            throw new Error("Database connection failed");
        }

        const db = client.db();
        this.collection = db.collection<INotification>("notifications");
        return this.collection;
    }

    public async save(data: Omit<INotification, "created_at" | "metadata">) {
        try {
            const col = await this.getCollection();
            await col.insertOne({
                ...data,
                metadata: {},
                created_at: Date.now()
            });
            logger.info("Notification saved successfully");
        } catch (error) {
            logger.error("Failed to save notification");
            throw error;
        }
    }

    public async getByUser(userId: string) {
        try {
            const col = await this.getCollection();
            return await col
                .find({ user_id: userId })
                .sort({ created_at: -1 })
                .toArray();
        } catch (error) {
            logger.error("Failed to fetch notifications");
            throw error;
        }
    }
}

export const notificationService = new NotificationService();
