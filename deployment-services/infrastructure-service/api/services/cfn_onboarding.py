"""F8 ticket 3 — CloudFormation onboarding completion ("Verify connection") and refresh.

See plan/F8-cloudformation-onboarding.md, Decision §4 ("Completing onboarding") and §5/§5a
("Policy refresh ... / Never-downgrade on template swap"). Both endpoints AssumeRole into
the customer's account through the exact same cross-account trust boundary every other
customer-account call uses (`api.cloud_providers.aws.authenticate`); nothing here grants a
new capability in the customer's account beyond what `LaunchpadDeploymentRole` already
allows.

`verify` claims the infra's onboarding token (burning it, same as the script callback)
and is the one place that proves the customer actually used the CFN path — `AssumeRole`
alone would also succeed against a script-created role, so the `launchpad:managed-by`
role tag is the real gate. `refresh` runs against an already-onboarded, already-
authenticated CFN infra and has no token to claim.

Security note (see the PR description for the full analysis): the sibling-wide
`policy_version`/`cfn_stack_name` write is scoped to `code=infra.code` — the AWS account
`AssumeRole` just proved this session is actually in, read from the row the caller owns —
intersected with `onboarding_method=CLOUDFORMATION`. A caller can only ever affect another
tenant's row if that tenant's infra already shares the same AWS account and already
completed CFN verify itself; that sharing is a pre-existing, intentional platform property
(the same account-is-the-sharing-boundary design `nuke_worker.py` already relies on), not
something this endpoint introduces.
"""

import logging
import secrets

import boto3
from api.cloud_providers.aws.iam_policy import generate as cfn_generate
from api.cloud_providers.aws.iam_policy.live_diff import ROLE_NAME
from api.repositories.infrastructure import InfrastructureRepository
from api.services.infrastructure import InfrastructureService
from botocore.config import Config
from botocore.exceptions import ClientError
from shared.enums.orchestrator import ComputeType, OnboardingMethod

logger = logging.getLogger(__name__)

# IAM resources are global; the CFN *stack* this template creates has to live somewhere,
# and every caller (quick-create link, verify, refresh, nuke) must agree on where to find
# it. Fixed platform constant, never derived from infra.metadata.aws_region (which means
# something unrelated — where data-plane resources provision). See plan §4.
CFN_STACK_REGION = "us-east-1"

MANAGED_BY_TAG_KEY = "launchpad:managed-by"
MANAGED_BY_TAG_VALUE = "cloudformation"
POLICY_VERSION_TAG_KEY = "launchpad:policy-version"
COMPUTE_TYPES_TAG_KEY = "launchpad:compute-types"
STACK_NAME_TAG_KEY = "launchpad:cfn-stack-name"

_CLIENT_CONFIG = Config(connect_timeout=5, read_timeout=8, retries={"max_attempts": 2})


class CfnOnboardingNotFound(Exception):
    """Infrastructure not found, or infra_id is not a valid UUID."""


class CfnOnboardingForbidden(Exception):
    """Authenticated user is not this infrastructure's owner."""


class CfnOnboardingRoleNotTagged(Exception):
    """AssumeRole succeeded but LaunchpadDeploymentRole isn't tagged as CFN-managed —
    the customer ran the script, not the CFN quick-create link."""


class CfnOnboardingMockUnsupported(Exception):
    """CloudFormation onboarding has no meaning for a mock (dev-mode) infrastructure."""


class CfnOnboardingNotCloudFormation(Exception):
    """Refresh was called against an infra that was never CFN-onboarded."""


class CfnOnboardingUnavailable(Exception):
    """A non-retryable AWS error occurred talking to the already-assumed session
    (ListRoleTags/DescribeStacks/CreateChangeSet). Carries the exception type name for
    logging; never the exception message, which can carry ARNs."""

    def __init__(self, exc_type_name: str):
        self.exc_type_name = exc_type_name
        super().__init__(exc_type_name)


def _iam_client_for(credentials):
    return boto3.client(
        "iam",
        aws_access_key_id=credentials["aws_access_key_id"],
        aws_secret_access_key=credentials["aws_secret_access_key"],
        aws_session_token=credentials.get("aws_session_token"),
        region_name=CFN_STACK_REGION,
        config=_CLIENT_CONFIG,
    )


def _cloudformation_client_for(credentials):
    return boto3.client(
        "cloudformation",
        aws_access_key_id=credentials["aws_access_key_id"],
        aws_secret_access_key=credentials["aws_secret_access_key"],
        aws_session_token=credentials.get("aws_session_token"),
        region_name=CFN_STACK_REGION,
        config=_CLIENT_CONFIG,
    )


def _role_tags(iam) -> dict:
    tags = iam.list_role_tags(RoleName=ROLE_NAME)["Tags"]
    return {t["Key"]: t["Value"] for t in tags}


def widest_compute_type(compute_types) -> str:
    """The widest compute_type across a set — mirrors create_aws_role.sh's
    EFFECTIVE_COMPUTE_TYPE: any eks sibling means the shared role must keep carrying the
    eks template (statements + MaxSessionDuration), never ecs_fargate alone. Only two
    compute types exist today, so "widest" is simply "is eks present"."""
    return ComputeType.EKS if ComputeType.EKS in compute_types else ComputeType.ECS_FARGATE


def _template_body_for(compute_type: str) -> str:
    """Reads the committed, generated CFN template for `compute_type` straight off
    disk — the same bytes `generate.py --check` drift-checks in CI. Ticket 4 (S3
    hosting for the quick-create link) hasn't shipped yet, so CreateChangeSet is called
    with `TemplateBody` instead of `TemplateURL`; TemplateURL's S3-only constraint is an
    API restriction on `CreateStack`'s browser-driven quick-create link, not on this
    boto3 call."""
    path = cfn_generate.CFN_TEMPLATE_PATHS[compute_type]
    return path.read_text()


class CfnOnboardingService:
    def __init__(self):
        self.repo = InfrastructureRepository()
        self.infra_service = InfrastructureService()

    def _get_owned_infra(self, user_id, infra_id):
        import uuid

        try:
            uuid.UUID(str(infra_id))
        except (ValueError, TypeError):
            raise CfnOnboardingNotFound()
        infra = self.repo.get_by_id(user_id, infra_id)
        if not infra:
            raise CfnOnboardingNotFound()
        if str(infra.user_id) != str(user_id):
            raise CfnOnboardingForbidden()
        return infra

    def _cfn_siblings(self, infra):
        """Every other infra sharing this AWS account (`infra.code` — the account
        AssumeRole just proved this session is in, not a client-supplied value) that
        has itself completed CFN verify (`onboarding_method=CLOUDFORMATION`, which is
        only ever set by a successful verify call) and is still authenticated. Excludes
        `infra` itself; callers add it back explicitly."""
        from api.models.infrastructure import Infrastructure as InfraModel

        return list(
            InfraModel.objects.filter(
                code=infra.code,
                onboarding_method=OnboardingMethod.CLOUDFORMATION,
                is_cloud_authenticated=True,
            ).exclude(id=infra.id).values_list("id", flat=True)
        )

    def verify(self, *, user_id, infra_id) -> dict:
        from api.models.infrastructure import Infrastructure as InfraModel

        infra = self._get_owned_infra(user_id, infra_id)

        if infra.is_mock:
            raise CfnOnboardingMockUnsupported()

        # Claims the onboarding token and runs the bounded-retry AssumeRole — the sole
        # idempotency gate for this endpoint (see plan §4: is_cloud_authenticated must
        # never also gate it, since nothing resets it on a later failure). Shared
        # exactly with the script callback; propagates OnboardingTokenClaimFailed /
        # OnboardingAssumeRoleValueError / OnboardingAssumeRoleFailed unchanged.
        credentials = self.infra_service.claim_and_authenticate(infra)

        try:
            iam = _iam_client_for(credentials)
            tags = _role_tags(iam)
        except ClientError as e:
            self.infra_service.release_onboarding_token_claim(infra.id)
            logger.warning("cfn-verify ListRoleTags failed for infra %s: %s", infra.id, type(e).__name__)
            raise CfnOnboardingUnavailable(type(e).__name__) from e

        if tags.get(MANAGED_BY_TAG_KEY) != MANAGED_BY_TAG_VALUE:
            self.infra_service.release_onboarding_token_claim(infra.id)
            raise CfnOnboardingRoleNotTagged()

        reported_policy_version = InfraModel.parse_reported_policy_version(tags.get(POLICY_VERSION_TAG_KEY))
        stack_name = tags.get(STACK_NAME_TAG_KEY) or None

        # Best-effort only: confirms the stack the tag points at is actually reachable,
        # but step above (the role tag) already proved this role is CFN-managed and
        # trusted — a DescribeStacks hiccup must never fail the call. See plan §4.
        if stack_name:
            try:
                cfn = _cloudformation_client_for(credentials)
                cfn.describe_stacks(StackName=stack_name)
            except Exception as e:
                logger.warning(
                    "cfn-verify best-effort DescribeStacks failed for infra %s stack %s: %s",
                    infra.id, stack_name, type(e).__name__,
                )

        InfraModel.objects.filter(id=infra.id).update(onboarding_method=OnboardingMethod.CLOUDFORMATION)
        sibling_ids = self._cfn_siblings(infra)

        result = self.infra_service.finish_onboarding(
            infra,
            policy_version=reported_policy_version,
            cfn_stack_name=stack_name,
            sibling_targets=sibling_ids,
        )
        result["policy_version"] = reported_policy_version
        result["cfn_stack_name"] = stack_name
        return result

    def refresh(self, *, user_id, infra_id) -> dict:
        from api.cloud_providers.aws.authenticate import assume_role_credentials_only
        from api.models.infrastructure import Infrastructure as InfraModel

        infra = self._get_owned_infra(user_id, infra_id)

        if infra.is_mock:
            raise CfnOnboardingMockUnsupported()
        if infra.onboarding_method != OnboardingMethod.CLOUDFORMATION:
            raise CfnOnboardingNotCloudFormation()

        # Refresh reuses a session Launchpad already legitimately holds for this infra —
        # not a new trust boundary, just exercising an existing one (plan §5). No
        # onboarding token to claim/retry: this infra is already onboarded.
        try:
            credentials = assume_role_credentials_only(infra)
        except Exception as e:
            logger.warning("cfn-refresh AssumeRole failed for infra %s: %s", infra.id, type(e).__name__)
            raise CfnOnboardingUnavailable(type(e).__name__) from e

        try:
            iam = _iam_client_for(credentials)
            tags = _role_tags(iam)
        except ClientError as e:
            logger.warning("cfn-refresh ListRoleTags failed for infra %s: %s", infra.id, type(e).__name__)
            raise CfnOnboardingUnavailable(type(e).__name__) from e

        if tags.get(MANAGED_BY_TAG_KEY) != MANAGED_BY_TAG_VALUE:
            raise CfnOnboardingRoleNotTagged()

        stack_name = tags.get(STACK_NAME_TAG_KEY) or infra.cfn_stack_name
        if not stack_name:
            raise CfnOnboardingUnavailable("StackNameUnknown")
        live_policy_version = InfraModel.parse_reported_policy_version(tags.get(POLICY_VERSION_TAG_KEY))

        # Ground truth for what's actually live comes from the tag just read through
        # the assumed session, never from the template this call is about to propose —
        # CreateChangeSet applies nothing yet, so writing the proposed version here
        # would clear the stale flag before the grants exist.
        sibling_ids = self._cfn_siblings(infra)
        self.infra_service.write_policy_state(
            infra, policy_version=live_policy_version, cfn_stack_name=stack_name,
            sibling_targets=sibling_ids,
        )

        # Never-downgrade: recommend the widest compute_type across every authenticated
        # sibling sharing this account, including infra itself — never infra's own
        # compute_type in isolation. See plan §5a.
        sibling_compute_types = {infra.compute_type}
        if sibling_ids:
            sibling_compute_types |= set(
                InfraModel.objects.filter(id__in=sibling_ids).values_list("compute_type", flat=True)
            )
        target_compute_type = widest_compute_type(sibling_compute_types)
        template_body = _template_body_for(target_compute_type)

        try:
            cfn = _cloudformation_client_for(credentials)
            response = cfn.create_change_set(
                StackName=stack_name,
                TemplateBody=template_body,
                UsePreviousTemplate=False,
                Parameters=[
                    {"ParameterKey": "ExternalIds", "UsePreviousValue": True},
                    {"ParameterKey": "LaunchpadPlatformAccountId", "UsePreviousValue": True},
                    {"ParameterKey": "LaunchpadPlatformUser", "UsePreviousValue": True},
                ],
                Capabilities=["CAPABILITY_NAMED_IAM"],
                # A random suffix, never a slice of infra.id: Infrastructure.id is a
                # UUIDv7 whose leading bits are a timestamp, and root CLAUDE.md's
                # uniqueness-key convention forbids using any prefix of it as one.
                ChangeSetName=f"launchpad-refresh-{secrets.token_hex(8)}",
                ChangeSetType="UPDATE",
            )
        except ClientError as e:
            logger.warning("cfn-refresh CreateChangeSet failed for infra %s: %s", infra.id, type(e).__name__)
            raise CfnOnboardingUnavailable(type(e).__name__) from e

        # Query-string shape is provisional — plan §5 flags the exact deep-link format
        # as unverified until the real-AWS checklist (ticket 8) confirms it.
        review_url = (
            f"https://{CFN_STACK_REGION}.console.aws.amazon.com/cloudformation/home"
            f"?region={CFN_STACK_REGION}#/stacks/changeset/review"
            f"?stackId={response['StackId']}&changeSetId={response['Id']}"
        )
        return {
            "infrastructure_id": str(infra.id),
            "stack_name": stack_name,
            "change_set_id": response["Id"],
            "target_compute_type": target_compute_type,
            "review_url": review_url,
        }
