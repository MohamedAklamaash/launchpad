import { EventPayload, OutboundEvent } from './event.types.js';

// F9 ticket 2: auth-service owns the canonical Organization/Membership/Invitation tables
// and publishes their changes here so infrastructure-service, application-service, and
// payment-service can mirror a read-model the same way infra.events is already mirrored
// (see infra.events.ts). Every payload below carries `version` — the per-Membership
// monotonic counter from host_readiness_version's pattern (plan/F9-teams-and-orgs.md,
// Decision: Cross-service sync) — and a consumer must reject any payload whose version is
// not strictly greater than what it already has stored, never treat a missing version as
// "always current".
export const ORG_EVENT_EXCHANGE = 'org.events';

export const ORG_CREATED_ROUTING_KEY = 'org.created';
export const ORG_MEMBERSHIP_CHANGED_ROUTING_KEY = 'org.membership.changed';
export const ORG_MEMBERSHIP_REMOVED_ROUTING_KEY = 'org.membership.removed';
export const ORG_INVITATION_ACCEPTED_ROUTING_KEY = 'org.invitation.accepted';

export interface OrgCreatedPayload extends EventPayload {
    organization_id: string;
    owner_account_id: string;
    version: number;
}

export interface OrgMembershipChangedPayload extends EventPayload {
    organization_id: string;
    account_id: string;
    role: string;
    version: number;
}

export interface OrgMembershipRemovedPayload extends EventPayload {
    organization_id: string;
    account_id: string;
    version: number;
}

export interface OrgInvitationAcceptedPayload extends EventPayload {
    organization_id: string;
    account_id: string;
    email: string;
    role: string;
    version: number;
}

export type OrgCreatedEvent = OutboundEvent<typeof ORG_CREATED_ROUTING_KEY, OrgCreatedPayload>;
export type OrgMembershipChangedEvent = OutboundEvent<
    typeof ORG_MEMBERSHIP_CHANGED_ROUTING_KEY,
    OrgMembershipChangedPayload
>;
export type OrgMembershipRemovedEvent = OutboundEvent<
    typeof ORG_MEMBERSHIP_REMOVED_ROUTING_KEY,
    OrgMembershipRemovedPayload
>;
export type OrgInvitationAcceptedEvent = OutboundEvent<
    typeof ORG_INVITATION_ACCEPTED_ROUTING_KEY,
    OrgInvitationAcceptedPayload
>;
