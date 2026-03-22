import { ObjectId } from 'mongodb';

export interface INotification {
    _id?: ObjectId;
    user_id?: string;
    user_name: string;
    email: string;
    infra_id: string;
    source: string;
    metadata: Record<string, unknown>;
    created_at: number;
}
