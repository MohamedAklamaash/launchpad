import { EventPayload, OutboundEvent } from './event.types.js';

export const INFRA_EVENT_EXCHANGE = 'infrastructure.events';
export const INFRA_CREATED_ROUTING_KEY = 'infrastructure.created';

export interface InfraCreatedPayload extends EventPayload {
    infra_id: string;
    user_id: string;
}

export type InfraCreatedEvent = OutboundEvent<
    typeof INFRA_CREATED_ROUTING_KEY,
    InfraCreatedPayload
>;
