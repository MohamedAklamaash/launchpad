"""F8 — `Infrastructure.onboarding_method` / `Infrastructure.cfn_stack_name`.

Both fields are additive: every pre-existing row (and any row created without passing
them) must read as the script path, since that was the only onboarding method before
this ticket. Also guards that the model and the committed migration (0038) stay in sync,
since a drifted migration is exactly the kind of thing `makemigrations --check` catches
in CI but a reviewer skimming a diff can miss.
"""

import uuid

import pytest
from django.core.management import call_command
from shared.enums.orchestrator import OnboardingMethod


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

    def _make(user=None, **kwargs):
        return Infrastructure.objects.create(
            user=user or make_user(),
            name=f"infra-{uuid.uuid4()}",
            cloud_provider="aws",
            max_cpu=1024,
            max_memory=512,
            code="123456789012",
            metadata={},
            **kwargs,
        )
    return _make


def test_new_infra_defaults_to_script_onboarding(make_infra):
    infra = make_infra()

    assert infra.onboarding_method == OnboardingMethod.SCRIPT
    assert infra.cfn_stack_name is None

    infra.refresh_from_db()
    assert infra.onboarding_method == OnboardingMethod.SCRIPT
    assert infra.cfn_stack_name is None


def test_cloudformation_onboarding_method_round_trips(make_infra):
    infra = make_infra(
        onboarding_method=OnboardingMethod.CLOUDFORMATION,
        cfn_stack_name="LaunchpadDeploymentRole-renamed",
    )

    infra.refresh_from_db()

    assert infra.onboarding_method == OnboardingMethod.CLOUDFORMATION
    assert infra.cfn_stack_name == "LaunchpadDeploymentRole-renamed"


def test_no_missing_migrations(db):
    """Guards against a model field drifting from the committed migration (0038) — the
    same check CI's `makemigrations --check` would fail on, run directly so a missing
    migration is caught locally before relying on CI."""
    call_command("makemigrations", "api", "--check", "--dry-run")


def test_migration_0038_adds_exactly_the_two_additive_fields():
    import importlib

    migration = importlib.import_module("api.migrations.0038_f8_onboarding_method_cfn_stack_name")
    operations = migration.Migration.operations

    assert len(operations) == 2
    added = {op.name: op for op in operations}
    assert set(added) == {"onboarding_method", "cfn_stack_name"}

    onboarding_field = added["onboarding_method"].field
    assert onboarding_field.default == OnboardingMethod.SCRIPT
    assert onboarding_field.null is False

    stack_name_field = added["cfn_stack_name"].field
    assert stack_name_field.null is True


def test_serializer_exposes_onboarding_method_and_stack_name(make_infra):
    """The dashboard branches its refresh/verify UI on these (F8 ticket 5); without them
    every infra reads as script-onboarded and the CFN UI is unreachable."""
    from api.serializers.infrastructure import InfrastructureSerializer

    script = InfrastructureSerializer.serialize_instance(make_infra())
    assert script["onboarding_method"] == "script"
    assert script["cfn_stack_name"] is None

    cfn = InfrastructureSerializer.serialize_instance(make_infra(
        onboarding_method=OnboardingMethod.CLOUDFORMATION, cfn_stack_name="my-stack",
    ))
    assert cfn["onboarding_method"] == "cloudformation"
    assert cfn["cfn_stack_name"] == "my-stack"
