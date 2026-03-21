export type EventPayload = Record<string, unknown>;

export interface DomainEvent<TType extends string, TPayload extends EventPayload> {
    type: TType;
    payload: TPayload;
    occured_at: string;
}

export interface EventMetadata {
    correlationId?: string;
    causationId?: string;
    version?: number;
}

export interface OutboundEvent<
    TType extends string,
    TPayload extends EventPayload,
> extends DomainEvent<TType, TPayload> {
    metadata?: EventMetadata;
}

export interface InBoundEvent<
    TType extends string,
    TPayload extends EventPayload,
> extends DomainEvent<TType, TPayload> {
    metadata: EventMetadata;
}
