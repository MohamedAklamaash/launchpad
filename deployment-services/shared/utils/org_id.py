import uuid

# Fixed, checked-in constant -- never an env var. Mirrors the pattern already used for
# the analogous Account id (identity-services/services/auth-service/src/utils/
# account-id.ts: ACCOUNT_NAMESPACE). infrastructure-service, application-service and
# payment-service must each compute the identical personal-org id independently, with
# no cross-service call at backfill time and no risk of drift between environments --
# an env-configurable namespace could silently diverge between services.
ORG_NAMESPACE = uuid.UUID("4c900569-d11e-46e2-b34c-cd677a57ca73")


def personal_org_id(legacy_user_id) -> uuid.UUID:
    """uuid5(ORG_NAMESPACE, legacy_user_id) -- the personal-org id for a pre-org owner.

    `legacy_user_id` is the pre-org `users.id` (auth-service) -- the same id every
    mirror already stores as `Infrastructure.user_id` (or, for a row with no `user_id`
    column of its own, reachable through its parent `Infrastructure`). Deliberately
    not keyed on the newer Account id, so this is computable from a column every
    service already has, with no dependency on any other service's backfill having run
    first -- what makes a three-service, no-coordination backfill safe to run in any
    order and idempotent on retry.

    Accepts a UUID or any string form `uuid.UUID()` parses; the round-trip through
    `uuid.UUID(...)` canonicalizes to lowercase-hyphenated before hashing, so the same
    id always hashes to the same result regardless of how it was stored or passed in.
    """
    canonical = str(uuid.UUID(str(legacy_user_id)))
    return uuid.uuid5(ORG_NAMESPACE, canonical)
