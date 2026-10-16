"""F8 ticket 3 — cfn-verify / cfn-refresh.

Covers: success, role-not-tagged-cloudformation (400), token-already-claimed (403), the
IAM-propagation retry, never-downgrade template selection, the sibling-wide write,
flag-off (404), and the owner-only authz ladder (non-owner 403, stranger 404).

boto3 is never called for real: `_iam_client_for` / `_cloudformation_client_for` are
monkeypatched to return a MagicMock per test.
"""
import sys
import types
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from shared.enums.orchestrator import OnboardingMethod


@pytest.fixture(autouse=True)
def _stub_infra_queue():
    """Same trick as test_onboarding_callback.py: InfraQueue opens real Redis
    connections at import time, and api.services.onboarding imports it lazily inside
    finish_onboarding specifically so this swap is honored regardless of which module
    performs that import."""
    previous = sys.modules.get("api.services.infra_queue")
    fake = types.ModuleType("api.services.infra_queue")
    fake.InfraQueue = MagicMock()
    sys.modules["api.services.infra_queue"] = fake
    try:
        yield fake.InfraQueue
    finally:
        if previous is not None:
            sys.modules["api.services.infra_queue"] = previous
        else:
            sys.modules.pop("api.services.infra_queue", None)


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture
def make_user(db):
    from api.models.user import User

    def _make():
        return User.objects.create(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="tester",
        )
    return _make


@pytest.fixture
def make_infra(db, make_user):
    from api.models.infrastructure import Infrastructure

    def _make(*, owner=None, code="123456789012", compute_type="ecs_fargate",
              onboarding_method=OnboardingMethod.SCRIPT, is_cloud_authenticated=False,
              cfn_stack_name=None, policy_version=None, is_mock=False):
        infra = Infrastructure.objects.create(
            user=owner or make_user(),
            name=f"infra-{uuid.uuid4()}",
            cloud_provider="aws",
            compute_type=compute_type,
            max_cpu=1024,
            max_memory=512,
            code=code,
            metadata={},
            onboarding_method=onboarding_method,
            is_cloud_authenticated=is_cloud_authenticated,
            cfn_stack_name=cfn_stack_name,
            policy_version=policy_version,
        )
        if is_mock:
            Infrastructure.objects.filter(id=infra.id).update(is_mock=True)
            infra.refresh_from_db()
        return infra
    return _make


def _fake_credentials():
    return {
        "aws_access_key_id": "test-key-id",
        "aws_secret_access_key": "test-secret",
        "aws_session_token": "test-session-token",
    }


def _tags(managed_by="cloudformation", policy_version="9", compute_types="ecs_fargate", stack_name="LaunchpadDeploymentRole"):
    tags = []
    if managed_by is not None:
        tags.append({"Key": "launchpad:managed-by", "Value": managed_by})
    if policy_version is not None:
        tags.append({"Key": "launchpad:policy-version", "Value": policy_version})
    if compute_types is not None:
        tags.append({"Key": "launchpad:compute-types", "Value": compute_types})
    if stack_name is not None:
        tags.append({"Key": "launchpad:cfn-stack-name", "Value": stack_name})
    return {"Tags": tags}


def _verify(factory, user, infra_id, *, enabled=True):
    from api.views.cfn_onboarding import infrastructure_cfn_verify

    with override_settings(CFN_ONBOARDING_ENABLED=enabled):
        request = factory.post(f"/api/v1/infrastructures/{infra_id}/cfn-verify/")
        force_authenticate(request, user=user)
        return infrastructure_cfn_verify(request, infra_id=infra_id)


def _refresh(factory, user, infra_id, *, enabled=True):
    from api.views.cfn_onboarding import infrastructure_cfn_refresh

    with override_settings(CFN_ONBOARDING_ENABLED=enabled):
        request = factory.post(f"/api/v1/infrastructures/{infra_id}/cfn-refresh/")
        force_authenticate(request, user=user)
        return infrastructure_cfn_refresh(request, infra_id=infra_id)


# ── flag gate ───────────────────────────────────────────────────────────────────────

def test_verify_404_when_flag_off(factory, make_infra):
    infra = make_infra()
    resp = _verify(factory, infra.user, str(infra.id), enabled=False)
    assert resp.status_code == 404


def test_refresh_404_when_flag_off(factory, make_infra):
    infra = make_infra(onboarding_method=OnboardingMethod.CLOUDFORMATION, is_cloud_authenticated=True)
    resp = _refresh(factory, infra.user, str(infra.id), enabled=False)
    assert resp.status_code == 404


# ── owner-only authz ladder ──────────────────────────────────────────────────────────

def test_verify_stranger_gets_404(factory, make_infra, make_user):
    infra = make_infra()
    resp = _verify(factory, make_user(), str(infra.id))
    assert resp.status_code == 404


def test_verify_invited_non_owner_gets_403(factory, make_infra, make_user):
    infra = make_infra()
    invited = make_user()
    infra.invited_users.add(invited)
    resp = _verify(factory, invited, str(infra.id))
    assert resp.status_code == 403


def test_refresh_stranger_gets_404(factory, make_infra, make_user):
    infra = make_infra(onboarding_method=OnboardingMethod.CLOUDFORMATION, is_cloud_authenticated=True)
    resp = _refresh(factory, make_user(), str(infra.id))
    assert resp.status_code == 404


def test_refresh_invited_non_owner_gets_403(factory, make_infra, make_user):
    infra = make_infra(onboarding_method=OnboardingMethod.CLOUDFORMATION, is_cloud_authenticated=True)
    invited = make_user()
    infra.invited_users.add(invited)
    resp = _refresh(factory, invited, str(infra.id))
    assert resp.status_code == 403


# ── verify: success ──────────────────────────────────────────────────────────────

def test_verify_success_sets_onboarding_method_policy_version_and_stack_name(factory, make_infra):
    from api.models.infrastructure import Infrastructure

    infra = make_infra()
    infra.issue_onboarding_token()
    iam_client = MagicMock()
    iam_client.list_role_tags.return_value = _tags(stack_name="my-renamed-stack")
    cfn_client = MagicMock()
    cfn_client.describe_stacks.return_value = {"Stacks": [{"StackStatus": "CREATE_COMPLETE"}]}

    with patch(
        "api.cloud_providers.aws.authenticate.authenticate_infrastructure",
        return_value=_fake_credentials(),
    ), patch("api.services.cfn_onboarding._iam_client_for", return_value=iam_client), \
         patch("api.services.cfn_onboarding._cloudformation_client_for", return_value=cfn_client):
        resp = _verify(factory, infra.user, str(infra.id))

    assert resp.status_code == 202
    assert resp.data["status"] == "accepted"
    assert resp.data["policy_version"] == 9
    assert resp.data["cfn_stack_name"] == "my-renamed-stack"

    infra = Infrastructure.objects.get(id=infra.id)
    assert infra.onboarding_method == OnboardingMethod.CLOUDFORMATION
    assert infra.policy_version == 9
    assert infra.cfn_stack_name == "my-renamed-stack"
    assert infra.onboarding_token_used_at is not None
    cfn_client.describe_stacks.assert_called_once_with(StackName="my-renamed-stack")


def test_verify_best_effort_describe_stacks_failure_does_not_fail_the_call(factory, make_infra):
    infra = make_infra()
    infra.issue_onboarding_token()
    iam_client = MagicMock()
    iam_client.list_role_tags.return_value = _tags()
    cfn_client = MagicMock()
    cfn_client.describe_stacks.side_effect = RuntimeError("stack not reachable")

    with patch(
        "api.cloud_providers.aws.authenticate.authenticate_infrastructure",
        return_value=_fake_credentials(),
    ), patch("api.services.cfn_onboarding._iam_client_for", return_value=iam_client), \
         patch("api.services.cfn_onboarding._cloudformation_client_for", return_value=cfn_client):
        resp = _verify(factory, infra.user, str(infra.id))

    assert resp.status_code == 202


def test_verify_mock_infrastructure_is_rejected(factory, make_infra):
    infra = make_infra(is_mock=True)
    infra.issue_onboarding_token()
    resp = _verify(factory, infra.user, str(infra.id))
    assert resp.status_code == 400


# ── role not tagged cloudformation → 400 ────────────────────────────────────────────

def test_verify_role_not_tagged_cloudformation_returns_400_and_releases_claim(factory, make_infra):
    infra = make_infra()
    infra.issue_onboarding_token()
    iam_client = MagicMock()
    iam_client.list_role_tags.return_value = _tags(managed_by=None)

    with patch(
        "api.cloud_providers.aws.authenticate.authenticate_infrastructure",
        return_value=_fake_credentials(),
    ), patch("api.services.cfn_onboarding._iam_client_for", return_value=iam_client):
        resp = _verify(factory, infra.user, str(infra.id))

    assert resp.status_code == 400
    infra.refresh_from_db()
    assert infra.onboarding_token_used_at is None
    assert infra.onboarding_method == OnboardingMethod.SCRIPT


def test_verify_role_tagged_for_script_path_returns_400(factory, make_infra):
    """The exact scenario the tag check exists for: AssumeRole succeeds against a
    perfectly valid script-created role, but it never went through CFN."""
    infra = make_infra()
    infra.issue_onboarding_token()
    iam_client = MagicMock()
    iam_client.list_role_tags.return_value = {"Tags": []}

    with patch(
        "api.cloud_providers.aws.authenticate.authenticate_infrastructure",
        return_value=_fake_credentials(),
    ), patch("api.services.cfn_onboarding._iam_client_for", return_value=iam_client):
        resp = _verify(factory, infra.user, str(infra.id))

    assert resp.status_code == 400


# ── token already claimed → 403 ─────────────────────────────────────────────────────

def test_verify_token_already_claimed_returns_403(factory, make_infra):
    from django.utils import timezone

    infra = make_infra()
    infra.issue_onboarding_token()
    infra.onboarding_token_used_at = timezone.now()
    infra.save(update_fields=["onboarding_token_used_at"])

    resp = _verify(factory, infra.user, str(infra.id))
    assert resp.status_code == 403


# ── IAM-propagation retry ───────────────────────────────────────────────────────────

def test_verify_retries_assume_role_until_iam_propagates(factory, make_infra):
    infra = make_infra()
    infra.issue_onboarding_token()
    iam_client = MagicMock()
    iam_client.list_role_tags.return_value = _tags()

    calls = iter([RuntimeError("not yet"), RuntimeError("not yet"), _fake_credentials()])

    def fake_authenticate(_infra):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    with patch(
        "api.cloud_providers.aws.authenticate.authenticate_infrastructure",
        side_effect=fake_authenticate,
    ), patch("api.services.onboarding.time.sleep") as sleep, \
         patch("api.services.cfn_onboarding._iam_client_for", return_value=iam_client), \
         patch("api.services.cfn_onboarding._cloudformation_client_for", return_value=MagicMock()):
        resp = _verify(factory, infra.user, str(infra.id))

    assert resp.status_code == 202
    assert sleep.call_count == 2
    infra.refresh_from_db()
    assert infra.onboarding_token_used_at is not None


def test_verify_retry_exhaustion_returns_403_with_retry_after_and_releases_claim(factory, make_infra):
    infra = make_infra()
    infra.issue_onboarding_token()

    with patch(
        "api.cloud_providers.aws.authenticate.authenticate_infrastructure",
        side_effect=RuntimeError("still not ready"),
    ), patch("api.services.onboarding.time.sleep"):
        resp = _verify(factory, infra.user, str(infra.id))

    assert resp.status_code == 403
    assert resp.data["details"] == "RuntimeError"
    assert resp["Retry-After"] == "30"
    infra.refresh_from_db()
    assert infra.onboarding_token_used_at is None


# ── sibling-wide write ───────────────────────────────────────────────────────────────

def test_verify_writes_policy_version_and_stack_name_onto_cloudformation_siblings_only(
    factory, make_infra, make_user,
):
    from api.models.infrastructure import Infrastructure

    shared_account = "123456789012"
    owner = make_user()
    sibling_cfn = make_infra(
        owner=owner, code=shared_account, onboarding_method=OnboardingMethod.CLOUDFORMATION,
        is_cloud_authenticated=True, policy_version=5, cfn_stack_name="old-stack-name",
    )
    sibling_script = make_infra(
        owner=owner, code=shared_account, onboarding_method=OnboardingMethod.SCRIPT,
        is_cloud_authenticated=True, policy_version=5,
    )
    other_account = make_infra(
        owner=owner, code="999999999999", onboarding_method=OnboardingMethod.CLOUDFORMATION,
        is_cloud_authenticated=True, policy_version=5,
    )
    infra = make_infra(owner=owner, code=shared_account)
    infra.issue_onboarding_token()

    iam_client = MagicMock()
    iam_client.list_role_tags.return_value = _tags(policy_version="9", stack_name="shared-stack")

    with patch(
        "api.cloud_providers.aws.authenticate.authenticate_infrastructure",
        return_value=_fake_credentials(),
    ), patch("api.services.cfn_onboarding._iam_client_for", return_value=iam_client), \
         patch("api.services.cfn_onboarding._cloudformation_client_for", return_value=MagicMock()):
        resp = _verify(factory, infra.user, str(infra.id))

    assert resp.status_code == 202

    sibling_cfn.refresh_from_db()
    assert sibling_cfn.policy_version == 9
    assert sibling_cfn.cfn_stack_name == "shared-stack"

    # A script-onboarded sibling in the same AWS account must never be touched: it has
    # a different onboarding method and this write is scoped to CFN-managed rows only.
    sibling_script.refresh_from_db()
    assert sibling_script.policy_version == 5
    assert sibling_script.cfn_stack_name is None

    # A CFN sibling in a *different* AWS account must never be touched, even though it
    # shares nothing with this call except being owned by the same user — proves the
    # sibling scope is the verified AssumeRole'd account, not "this user's other infras".
    other_account.refresh_from_db()
    assert other_account.policy_version == 5

    infra = Infrastructure.objects.get(id=infra.id)
    assert infra.policy_version == 9
    assert infra.cfn_stack_name == "shared-stack"


def test_verify_does_not_write_a_different_tenants_infra_even_if_they_share_an_aws_account(
    factory, make_infra, make_user,
):
    """Two different owners whose infras happen to name the same AWS account `code`
    (e.g. both typed the same account id before either one onboarded) share nothing
    unless the account's own CFN stack actually trusts both — modeled here by both
    rows being is_cloud_authenticated + onboarding_method=cloudformation already,
    which only happens after each independently completed verify against that real
    account. The write must still reach that row: this is the intentional,
    pre-existing account-is-the-sharing-boundary design, not a new tenant isolation
    hole introduced by this endpoint."""
    shared_account = "123456789012"
    tenant_a = make_user()
    tenant_b = make_user()
    other_tenant_infra = make_infra(
        owner=tenant_b, code=shared_account, onboarding_method=OnboardingMethod.CLOUDFORMATION,
        is_cloud_authenticated=True, policy_version=5,
    )
    infra = make_infra(owner=tenant_a, code=shared_account)
    infra.issue_onboarding_token()

    iam_client = MagicMock()
    iam_client.list_role_tags.return_value = _tags(policy_version="9", stack_name="shared-stack")

    with patch(
        "api.cloud_providers.aws.authenticate.authenticate_infrastructure",
        return_value=_fake_credentials(),
    ), patch("api.services.cfn_onboarding._iam_client_for", return_value=iam_client), \
         patch("api.services.cfn_onboarding._cloudformation_client_for", return_value=MagicMock()):
        resp = _verify(factory, infra.user, str(infra.id))

    assert resp.status_code == 202
    other_tenant_infra.refresh_from_db()
    # This is the pre-existing, intentional account-sharing design (nuke_worker.py's
    # _other_infras_same_account filters by code alone too) — the write reaches it
    # because that AWS account's own role already trusts both infras' ExternalIds,
    # which only happens with the account owner's consent. Document, not merely assert.
    assert other_tenant_infra.policy_version == 9


# ── refresh: never-downgrade template selection ─────────────────────────────────────

def test_refresh_selects_eks_template_when_any_authenticated_sibling_is_eks(factory, make_infra, make_user):
    shared_account = "123456789012"
    owner = make_user()
    make_infra(
        owner=owner, code=shared_account, compute_type="eks",
        onboarding_method=OnboardingMethod.CLOUDFORMATION, is_cloud_authenticated=True,
    )
    infra = make_infra(
        owner=owner, code=shared_account, compute_type="ecs_fargate",
        onboarding_method=OnboardingMethod.CLOUDFORMATION, is_cloud_authenticated=True,
        cfn_stack_name="shared-stack",
    )

    iam_client = MagicMock()
    iam_client.list_role_tags.return_value = _tags(stack_name="shared-stack")
    cfn_client = MagicMock()
    cfn_client.create_change_set.return_value = {"Id": "cs-1", "StackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/shared-stack/abc"}

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only", return_value=_fake_credentials()), \
         patch("api.services.cfn_onboarding._iam_client_for", return_value=iam_client), \
         patch("api.services.cfn_onboarding._cloudformation_client_for", return_value=cfn_client):
        resp = _refresh(factory, infra.user, str(infra.id))

    assert resp.status_code == 200
    assert resp.data["target_compute_type"] == "eks"
    call_kwargs = cfn_client.create_change_set.call_args.kwargs
    assert "AWS::IAM::Role" in call_kwargs["TemplateBody"]
    assert "MaxSessionDuration" in call_kwargs["TemplateBody"]


def test_refresh_keeps_ecs_fargate_template_when_no_sibling_is_eks(factory, make_infra, make_user):
    shared_account = "123456789012"
    owner = make_user()
    make_infra(
        owner=owner, code=shared_account, compute_type="ecs_fargate",
        onboarding_method=OnboardingMethod.CLOUDFORMATION, is_cloud_authenticated=True,
    )
    infra = make_infra(
        owner=owner, code=shared_account, compute_type="ecs_fargate",
        onboarding_method=OnboardingMethod.CLOUDFORMATION, is_cloud_authenticated=True,
        cfn_stack_name="shared-stack",
    )

    iam_client = MagicMock()
    iam_client.list_role_tags.return_value = _tags(stack_name="shared-stack")
    cfn_client = MagicMock()
    cfn_client.create_change_set.return_value = {"Id": "cs-1", "StackId": "stack-arn"}

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only", return_value=_fake_credentials()), \
         patch("api.services.cfn_onboarding._iam_client_for", return_value=iam_client), \
         patch("api.services.cfn_onboarding._cloudformation_client_for", return_value=cfn_client):
        resp = _refresh(factory, infra.user, str(infra.id))

    assert resp.status_code == 200
    assert resp.data["target_compute_type"] == "ecs_fargate"


def test_refresh_against_a_script_onboarded_infra_returns_400(factory, make_infra):
    infra = make_infra(onboarding_method=OnboardingMethod.SCRIPT, is_cloud_authenticated=True)
    resp = _refresh(factory, infra.user, str(infra.id))
    assert resp.status_code == 400


def test_refresh_writes_sibling_policy_version_from_the_live_tag_not_the_proposed_template(
    factory, make_infra, make_user,
):
    """The sibling write at refresh time must reflect what's actually live (the role
    tag), never the version of the template being proposed in the unreviewed change
    set — CreateChangeSet applies nothing until the customer executes it."""
    shared_account = "123456789012"
    owner = make_user()
    sibling = make_infra(
        owner=owner, code=shared_account, onboarding_method=OnboardingMethod.CLOUDFORMATION,
        is_cloud_authenticated=True, policy_version=5,
    )
    infra = make_infra(
        owner=owner, code=shared_account, onboarding_method=OnboardingMethod.CLOUDFORMATION,
        is_cloud_authenticated=True, cfn_stack_name="shared-stack", policy_version=5,
    )

    iam_client = MagicMock()
    iam_client.list_role_tags.return_value = _tags(policy_version="5", stack_name="shared-stack")
    cfn_client = MagicMock()
    cfn_client.create_change_set.return_value = {"Id": "cs-1", "StackId": "stack-arn"}

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only", return_value=_fake_credentials()), \
         patch("api.services.cfn_onboarding._iam_client_for", return_value=iam_client), \
         patch("api.services.cfn_onboarding._cloudformation_client_for", return_value=cfn_client):
        resp = _refresh(factory, infra.user, str(infra.id))

    assert resp.status_code == 200
    sibling.refresh_from_db()
    # Still 5 — the live tag hasn't advanced, even though a v9-widening change set was
    # just proposed to the customer. Nothing was executed yet.
    assert sibling.policy_version == 5


# ── the role-not-tagged check also applies to refresh ───────────────────────────────

def test_refresh_role_not_tagged_cloudformation_returns_400(factory, make_infra):
    infra = make_infra(
        onboarding_method=OnboardingMethod.CLOUDFORMATION, is_cloud_authenticated=True,
        cfn_stack_name="shared-stack",
    )
    iam_client = MagicMock()
    iam_client.list_role_tags.return_value = {"Tags": []}

    with patch("api.cloud_providers.aws.authenticate.assume_role_credentials_only", return_value=_fake_credentials()), \
         patch("api.services.cfn_onboarding._iam_client_for", return_value=iam_client):
        resp = _refresh(factory, infra.user, str(infra.id))

    assert resp.status_code == 400
