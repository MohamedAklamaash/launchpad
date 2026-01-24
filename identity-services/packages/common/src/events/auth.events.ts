import { EventPayload, OutboundEvent } from './event.types';

export const AUTH_EVENT_EXCHANGE = 'auth.events';
export const AUTH_USER_REGISTERED_ROUTING_KEY = 'auth.user.registered';
export const NOTIFICATION_EVENT_QUEUE = "notification-event";
export const AUTHENTICATE_INVITED_USER_EVENT = "authenticate-invited-user";

export interface AuthUserRegisteredPayload extends EventPayload {
    id: string;
    email: string;
    displayName: string;
    createdAt: string;
}

export type AuthRegisteredEvent = OutboundEvent<
    typeof AUTH_USER_REGISTERED_ROUTING_KEY,
    AuthUserRegisteredPayload
>;