"""Shared onboarding completion logic: claim the single-use onboarding token, run the
bounded-retry AssumeRole, and run the post-AssumeRole tail (policy_version write,
POLICY_STALE reenqueue, infra.created publish, enqueue provisioning).

Extracted out of `infrastructure_onboarding_callback` (F8 ticket 3) so the script
callback and the new CFN `cfn-verify` endpoint share the exact same claim ordering and
retry behaviour instead of maintaining two copies that can drift. `InfrastructureService`
exposes this as `claim_and_authenticate` / `finish_onboarding`; the functions live here,
not inlined on that class, to keep that file from growing past a few hundred lines.

Import locality matters here: `authenticate_infrastructure` and `InfraQueue` are
imported *inside* the functions that use them, not at module top. The existing
onboarding-callback test suite patches `api.cloud_providers.aws.authenticate
.authenticate_infrastructure` and relies on an autouse fixture that swaps
`sys.modules["api.services.infra_queue"]` before the view runs — a module-level import
here would bind the real objects before either patch takes effect. `time.sleep` is
called via `import time` (module attribute access), never `from time import sleep`,
for the same reason: `api.views.infrastructure.time.sleep` is patched directly.
"""

import logging
import time

from django.utils import timezone

logger = logging.getLogger(__name__)


class OnboardingTokenClaimFailed(Exception):
    """The atomic onboarding-token claim lost the race: already claimed by an earlier
    or concurrent call. Callers map this to 403."""


class OnboardingAssumeRoleValueError(Exception):
    """`authenticate_infrastructure` raised ValueError — a non-retryable input error
    (e.g. wrong cloud_provider), not an IAM propagation race. The token claim has
    already been released. Callers map this to 400."""


class OnboardingAssumeRoleFailed(Exception):
    """The bounded-retry AssumeRole exhausted every attempt. The token claim has
    already been released. Callers map this to 403 with Retry-After: 30."""

    def __init__(self, exc_type_name: str):
        self.exc_type_name = exc_type_name
        super().__init__(exc_type_name)


class OnboardingFinishFailed(Exception):
    """The tail's enqueue step failed. The token claim has already been released so a
    retry can simply re-run the callback. Callers map this to 500."""


def release_onboarding_token_claim(infra_id) -> None:
    """Releases a claim made by `claim_and_authenticate`, so a transient failure
    doesn't permanently brick onboarding. Idempotent."""
    from api.models.infrastructure import Infrastructure as InfraModel
    InfraModel.objects.filter(id=infra_id).update(onboarding_token_used_at=None)


def claim_and_authenticate(infra):
    """Atomically claims `infra`'s single-use onboarding token, then runs the
    bounded-retry AssumeRole against the customer's account.

    The conditional UPDATE is the sole idempotency gate — two concurrent callers can
    both pass earlier pre-checks, but only one wins this claim. The retry exists for
    IAM's eventual consistency right after role/policy creation; a ValueError from
    `authenticate_infrastructure` is treated as non-retryable.

    Returns the AssumeRole credentials dict on success. Raises
    OnboardingTokenClaimFailed / OnboardingAssumeRoleValueError /
    OnboardingAssumeRoleFailed on failure; the token claim is already released by the
    time any of these raise.
    """
    from api.cloud_providers.aws.authenticate import authenticate_infrastructure
    from api.models.infrastructure import Infrastructure as InfraModel

    claimed = InfraModel.objects.filter(
        id=infra.id, onboarding_token_used_at__isnull=True,
    ).update(onboarding_token_used_at=timezone.now())
    if claimed == 0:
        raise OnboardingTokenClaimFailed()

    last_exc = None
    for attempt in range(3):
        try:
            return authenticate_infrastructure(infra)
        except ValueError as e:
            release_onboarding_token_claim(infra.id)
            raise OnboardingAssumeRoleValueError(str(e)) from e
        except Exception as e:
            last_exc = e
            logger.warning(
                f"Onboarding AssumeRole attempt {attempt + 1}/3 failed for infra {infra.id}: {e}"
            )
            if attempt < 2:
                time.sleep(2)

    release_onboarding_token_claim(infra.id)
    raise OnboardingAssumeRoleFailed(type(last_exc).__name__)


def write_policy_state(infra, *, policy_version=None, cfn_stack_name=None, sibling_targets=None) -> None:
    """Writes the reported `policy_version` (and, for CFN, `cfn_stack_name`) onto
    `infra` and every id in `sibling_targets`, then reenqueues any POLICY_STALE cert
    for each row touched.

    `sibling_targets` defaults to none, which reproduces the script path's existing,
    documented behaviour of writing only the calling infra's row — a pre-existing gap
    this ticket does not fix for that path (see plan/F8-cloudformation-onboarding.md).
    Shared by `finish_onboarding` (the onboarding tail) and the CFN refresh endpoint,
    which writes the same two fields without running the rest of the onboarding tail
    (no token claim to release, no infra.created, no enqueue_provision — the infra was
    already onboarded).
    """
    from api.models.infrastructure import Infrastructure as InfraModel

    target_ids = {infra.id} | set(sibling_targets or [])
    update_fields = {}
    if policy_version is not None:
        update_fields["policy_version"] = policy_version
    if cfn_stack_name is not None:
        update_fields["cfn_stack_name"] = cfn_stack_name
    if update_fields:
        InfraModel.objects.filter(id__in=target_ids).update(**update_fields)
        if policy_version is not None:
            from api.services.cert_bootstrap import maybe_reenqueue_after_policy_refresh
            for target_id in target_ids:
                maybe_reenqueue_after_policy_refresh(target_id)


def finish_onboarding(infra, *, policy_version=None, cfn_stack_name=None, sibling_targets=None) -> dict:
    """Post-AssumeRole tail shared by both onboarding paths: writes the reported
    `policy_version` (and, for CFN, `cfn_stack_name`) onto `infra` and every id in
    `sibling_targets` (see `write_policy_state`), publishes infra.created, and enqueues
    provisioning.

    On enqueue failure, releases the token claim and raises OnboardingFinishFailed.
    """
    write_policy_state(
        infra, policy_version=policy_version, cfn_stack_name=cfn_stack_name,
        sibling_targets=sibling_targets,
    )

    try:
        from api.messaging.producer.producer import infra_producer
        infra_producer.publish_infra_created(
            user_id=infra.user_id,
            infra_id=infra.id,
            name=infra.name,
            cloud_provider=infra.cloud_provider,
            compute_type=infra.compute_type,
            max_cpu=infra.max_cpu,
            max_memory=infra.max_memory,
            code=infra.code,
            is_cloud_authenticated=True,
            is_mock=infra.is_mock,
            metadata=infra.metadata or {},
        )
    except Exception:
        logger.exception(f"Onboarding completion failed to publish infra.created for {infra.id}")
        # Don't fail the call — provisioning can still proceed; ops will need to backfill the read-model.

    from api.services.infra_queue import InfraQueue
    try:
        logger.info(f"onboarding completed for infra {infra.id}")
        was_enqueued = InfraQueue.enqueue_provision(str(infra.id))
    except Exception:
        logger.exception(f"Onboarding completion enqueue failed for infra {infra.id}")
        release_onboarding_token_claim(infra.id)
        raise OnboardingFinishFailed() from None

    if not was_enqueued:
        return {'status': 'already_queued', 'infrastructure_id': str(infra.id), 'is_cloud_authenticated': True}
    return {'status': 'accepted', 'infrastructure_id': str(infra.id), 'is_cloud_authenticated': True}
