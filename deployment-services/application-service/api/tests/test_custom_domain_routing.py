"""ALB wiring for VALIDATED custom domains: attach (SNI cap, host-forward/redirect rules,
idempotency, cross-tenant conflict, cert_arn/hostname validation, reservation-first with
compensation on failure) and detach (idempotent, scoped by (infrastructure_id, hostname),
only drops the local row once the AWS side is confirmed clean) — see
plan/F1b-tls-activation.md's Part 3b design."""
import uuid
from unittest.mock import patch

import pytest

from api.services import custom_domain_routing as routing

CERT_ARN_A = "arn:aws:acm:us-east-1:123456789012:certificate/aaaaaaaa"
CERT_ARN_B = "arn:aws:acm:us-east-1:123456789012:certificate/bbbbbbbb"


@pytest.fixture
def fixtures(schema_db):
    from api.mock.mock_session import MockSession
    from api.models.application import Application
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
    infra = Infrastructure.objects.create(
        id=uuid.uuid4(), user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=1024, max_memory=512,
    )
    Environment.objects.create(
        id=uuid.uuid4(), infrastructure=infra,
        alb_arn="arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/x/1",
    )
    app = Application.objects.create(
        id=uuid.uuid4(), user=user, infrastructure=infra, name="my-app",
        project_remote_url="https://github.com/x/y", project_branch="main",
        project_commit_hash="", envs={}, port=8080, target_group_arn="arn:tg",
        alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
    )
    session = MockSession(region="us-east-1", account_id="123456789012", infra_id=str(infra.id))
    return infra, app, session


@pytest.fixture(autouse=True)
def _mock_session(fixtures):
    _infra, _app, session = fixtures
    with patch("aws.session.create_boto3_session", return_value=session):
        yield


@pytest.mark.django_db
def test_attach_creates_forward_and_redirect_rules(fixtures):
    infra, app, _session = fixtures

    result = routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="app.example.com", cert_arn=CERT_ARN_A,
    )

    assert result["host_forward_rule_arn"]
    assert result["host_redirect_rule_arn"]

    from api.models.custom_domain_route import CustomDomainRoute
    route = CustomDomainRoute.objects.get(hostname="app.example.com")
    assert str(route.application_id) == str(app.id)
    assert route.cert_arn == CERT_ARN_A


@pytest.mark.django_db
def test_attach_rejects_malformed_cert_arn(fixtures):
    infra, app, _session = fixtures

    with pytest.raises(routing.CustomDomainAttachError):
        routing.attach_custom_domain(
            infrastructure_id=infra.id, application_id=app.id,
            hostname="app.example.com", cert_arn="not-an-arn",
        )

    from api.models.custom_domain_route import CustomDomainRoute
    assert not CustomDomainRoute.objects.filter(hostname="app.example.com").exists()


@pytest.mark.django_db
def test_attach_rejects_invalid_hostname(fixtures):
    infra, app, _session = fixtures

    with pytest.raises(routing.CustomDomainAttachError):
        routing.attach_custom_domain(
            infrastructure_id=infra.id, application_id=app.id,
            hostname="not_ldh_.example.com", cert_arn=CERT_ARN_A,
        )


@pytest.mark.django_db
def test_attach_rejects_cert_arn_from_a_different_aws_account(fixtures):
    from api.models.infrastructure import Infrastructure

    infra, app, _session = fixtures
    Infrastructure.objects.filter(id=infra.id).update(code="999999999999", is_mock=False)
    infra.refresh_from_db()

    with pytest.raises(routing.CustomDomainAttachError):
        routing.attach_custom_domain(
            infrastructure_id=infra.id, application_id=app.id,
            hostname="app.example.com", cert_arn=CERT_ARN_A,  # account 123456789012
        )


@pytest.mark.django_db
def test_attach_is_idempotent_for_same_application(fixtures):
    infra, app, _session = fixtures

    first = routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="app.example.com", cert_arn=CERT_ARN_A,
    )
    second = routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="app.example.com", cert_arn=CERT_ARN_A,
    )

    assert first == second

    from api.models.custom_domain_route import CustomDomainRoute
    assert CustomDomainRoute.objects.filter(hostname="app.example.com").count() == 1


@pytest.mark.django_db
def test_attach_raises_conflict_for_a_different_application(fixtures):
    from api.models.application import Application

    infra, app, _session = fixtures
    other_app = Application.objects.create(
        id=uuid.uuid4(), user=app.user, infrastructure=infra, name="other-app",
        project_remote_url="https://github.com/x/y", project_branch="main",
        project_commit_hash="", envs={}, port=8080, target_group_arn="arn:tg2",
        alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
    )
    routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="app.example.com", cert_arn=CERT_ARN_A,
    )

    with pytest.raises(routing.CustomDomainConflict):
        routing.attach_custom_domain(
            infrastructure_id=infra.id, application_id=other_app.id,
            hostname="app.example.com", cert_arn=CERT_ARN_A,
        )


@pytest.mark.django_db
def test_attach_raises_not_found_for_unknown_application(fixtures):
    infra, _app, _session = fixtures

    with pytest.raises(routing.CustomDomainNotFound):
        routing.attach_custom_domain(
            infrastructure_id=infra.id, application_id=uuid.uuid4(),
            hostname="app.example.com", cert_arn=CERT_ARN_A,
        )


@pytest.mark.django_db
def test_attach_raises_not_ready_without_alb(fixtures):
    from api.models.application import Application
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
    bare_infra = Infrastructure.objects.create(
        id=uuid.uuid4(), user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
        max_cpu=1024, max_memory=512,
    )
    app = Application.objects.create(
        id=uuid.uuid4(), user=user, infrastructure=bare_infra, name="bare-app",
        project_remote_url="https://github.com/x/y", project_branch="main",
        project_commit_hash="", envs={}, port=8080, target_group_arn="arn:tg",
        alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
    )

    with pytest.raises(routing.CustomDomainNotReady):
        routing.attach_custom_domain(
            infrastructure_id=bare_infra.id, application_id=app.id,
            hostname="app.example.com", cert_arn=CERT_ARN_A,
        )

    # Compensated: no reservation left behind for a failed attach.
    from api.models.custom_domain_route import CustomDomainRoute
    assert not CustomDomainRoute.objects.filter(hostname="app.example.com").exists()


@pytest.mark.django_db
def test_attach_raises_not_ready_without_target_group(fixtures):
    from api.models.application import Application

    infra, app, _session = fixtures
    no_deploy_app = Application.objects.create(
        id=uuid.uuid4(), user=app.user, infrastructure=infra, name="never-deployed",
        project_remote_url="https://github.com/x/y", project_branch="main",
        project_commit_hash="", envs={}, port=8080, target_group_arn="",
        alloted_cpu=256, alloted_memory=512, attached_database_ids=[],
    )

    with pytest.raises(routing.CustomDomainNotReady):
        routing.attach_custom_domain(
            infrastructure_id=infra.id, application_id=no_deploy_app.id,
            hostname="app.example.com", cert_arn=CERT_ARN_A,
        )


@pytest.mark.django_db
def test_attach_raises_sni_cap_reached(fixtures, settings):
    settings.MAX_SNI_CERTIFICATES_PER_LISTENER = 1
    infra, app, _session = fixtures

    routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="a.example.com", cert_arn=CERT_ARN_A,
    )

    with pytest.raises(routing.SniCapReached):
        routing.attach_custom_domain(
            infrastructure_id=infra.id, application_id=app.id,
            hostname="b.example.com", cert_arn=CERT_ARN_B,
        )

    from api.models.custom_domain_route import CustomDomainRoute
    assert not CustomDomainRoute.objects.filter(hostname="b.example.com").exists()


@pytest.mark.django_db
def test_detach_removes_route_and_no_ops_when_missing(fixtures):
    infra, app, _session = fixtures
    routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="app.example.com", cert_arn=CERT_ARN_A,
    )

    assert routing.detach_custom_domain(infra.id, "app.example.com") is True
    assert routing.detach_custom_domain(infra.id, "app.example.com") is True  # already gone — still success

    from api.models.custom_domain_route import CustomDomainRoute
    assert not CustomDomainRoute.objects.filter(hostname="app.example.com").exists()


@pytest.mark.django_db
def test_detach_is_scoped_by_infrastructure_id_not_hostname_alone(fixtures):
    """A stale/retried detach for the OLD owner of a hostname must never touch a
    different infrastructure's current attachment of the same hostname (security review
    R2)."""
    infra, app, _session = fixtures
    routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="app.example.com", cert_arn=CERT_ARN_A,
    )

    result = routing.detach_custom_domain(uuid.uuid4(), "app.example.com")

    assert result is True  # nothing found for THAT infra — a clean no-op, not an error
    from api.models.custom_domain_route import CustomDomainRoute
    assert CustomDomainRoute.objects.filter(hostname="app.example.com").exists()  # untouched


@pytest.mark.django_db
def test_detach_keeps_the_route_row_when_aws_side_fails(fixtures):
    """Security review R2: the old behavior always deleted the row even when the AWS
    calls failed, silently orphaning a still-attached SNI cert/rule and permanently
    blocking that hostname from ever being re-attached (the route row itself, unique on
    hostname, would make a future attach 409 forever). The row must survive a failed
    detach so the next sweep can retry it."""
    infra, app, _session = fixtures
    routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="app.example.com", cert_arn=CERT_ARN_A,
    )

    with patch("aws.session.create_boto3_session", side_effect=RuntimeError("boom")):
        assert routing.detach_custom_domain(infra.id, "app.example.com") is False

    from api.models.custom_domain_route import CustomDomainRoute
    assert CustomDomainRoute.objects.filter(hostname="app.example.com").exists()


@pytest.mark.django_db
def test_detach_custom_domains_for_application_returns_only_confirmed_hostnames(fixtures):
    infra, app, _session = fixtures
    routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="a.example.com", cert_arn=CERT_ARN_A,
    )
    routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="b.example.com", cert_arn=CERT_ARN_B,
    )

    hostnames = routing.detach_custom_domains_for_application(app.id)

    assert set(hostnames) == {"a.example.com", "b.example.com"}
    from api.models.custom_domain_route import CustomDomainRoute
    assert CustomDomainRoute.objects.filter(application_id=app.id).count() == 0


@pytest.mark.django_db
def test_detach_custom_domains_for_application_excludes_failed_detaches(fixtures):
    infra, app, _session = fixtures
    routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="a.example.com", cert_arn=CERT_ARN_A,
    )

    with patch("aws.session.create_boto3_session", side_effect=RuntimeError("boom")):
        hostnames = routing.detach_custom_domains_for_application(app.id)

    assert hostnames == []
    from api.models.custom_domain_route import CustomDomainRoute
    assert CustomDomainRoute.objects.filter(application_id=app.id).count() == 1


@pytest.mark.django_db
def test_notify_infrastructure_service_is_best_effort_on_failure():
    with patch("shared.resilience.http_client.ResilientHttpClient.post", side_effect=RuntimeError("down")):
        # Must not raise.
        routing.notify_infrastructure_service_of_deleted_application(uuid.uuid4(), uuid.uuid4())
