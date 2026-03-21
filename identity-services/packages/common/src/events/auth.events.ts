import { EventPayload, OutboundEvent } from './event.types.js';

export const AUTH_EVENT_EXCHANGE = 'auth.events';
export const AUTH_USER_REGISTERED_ROUTING_KEY = 'auth.user.registered';
export const NOTIFICATION_EVENT_QUEUE = 'notification-event';
export const AUTHENTICATE_INVITED_USER_EVENT = 'authenticate-invited-user';
export const FORGOT_PASSWORD_EVENT = 'forgot-password-event';

export interface AuthUserRegisteredPayload extends EventPayload {
    id: string;
    email: string;
    user_name: string;
    created_at: Date;
    infra_id: string[];
    role: string;
    profile_url?: string;
    updated_at: Date;
    metadata?: Record<string, any>;
    invited_by?: string;
}

export type AuthRegisteredEvent = OutboundEvent<
    typeof AUTH_USER_REGISTERED_ROUTING_KEY,
    AuthUserRegisteredPayload
>;
