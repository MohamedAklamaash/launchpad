import { v5 as uuidv5 } from 'uuid';

// Fixed, checked-in constant — never an env var. Mirrors ACCOUNT_NAMESPACE
// (utils/account-id.ts) and must byte-for-byte match
// deployment-services/shared/utils/org_id.py's ORG_NAMESPACE: infrastructure-service,
// application-service, and payment-service each compute this service's personal-org id
// independently, with no cross-service call, so a drifted namespace here would silently
// desync three already-deployed databases from auth-service's own canonical Organization
// row (plan/F9-teams-and-orgs.md, Decision: Data model).
export const ORG_NAMESPACE = '4c900569-d11e-46e2-b34c-cd677a57ca73';

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Mirrors org_id.py's `str(uuid.UUID(str(legacy_user_id)))` canonicalization — lowercase,
// hyphenated — so the same id always hashes to the same result regardless of casing.
// Throws rather than silently hashing a malformed id into a well-formed-looking but wrong
// org id.
const canonicalize = (legacyUserId: string): string => {
    const normalized = legacyUserId.trim().toLowerCase();
    if (!UUID_PATTERN.test(normalized)) {
        throw new Error(`personalOrgId: not a well-formed UUID: ${legacyUserId}`);
    }
    return normalized;
};

// uuid5(ORG_NAMESPACE, legacy_user_id) — the personal-org id for a pre-org owner.
// `legacyUserId` is the pre-org `users.id`/`invited_users.id` (auth-service), the same id
// every Django mirror already stores as `Infrastructure.user_id`. Deliberately not keyed
// on the newer Account id — see shared/utils/org_id.py's personal_org_id for the full
// rationale (computable with no dependency on any other service's backfill having run).
export const personalOrgId = (legacyUserId: string): string =>
    uuidv5(canonicalize(legacyUserId), ORG_NAMESPACE);
