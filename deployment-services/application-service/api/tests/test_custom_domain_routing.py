"""ALB wiring for VALIDATED custom domains: attach (SNI cap, host-forward/redirect rules,
idempotency, cross-tenant conflict) and detach (idempotent, always drops the local row even
when the AWS side best-effort fails) — see plan/F1b-tls-activation.md's Part 3b design."""
import uuid
from unittest.mock import patch

import pytest

from api.services import custom_domain_routing as routing


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
        hostname="app.example.com", cert_arn="arn:aws:acm:x:1:certificate/a",
    )

    assert result["host_forward_rule_arn"]
    assert result["host_redirect_rule_arn"]

    from api.models.custom_domain_route import CustomDomainRoute
    route = CustomDomainRoute.objects.get(hostname="app.example.com")
    assert str(route.application_id) == str(app.id)
    assert route.cert_arn == "arn:aws:acm:x:1:certificate/a"


@pytest.mark.django_db
def test_attach_is_idempotent_for_same_application(fixtures):
    infra, app, _session = fixtures

    first = routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="app.example.com", cert_arn="arn:a",
    )
    second = routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="app.example.com", cert_arn="arn:a",
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
        hostname="app.example.com", cert_arn="arn:a",
    )

    with pytest.raises(routing.CustomDomainConflict):
        routing.attach_custom_domain(
            infrastructure_id=infra.id, application_id=other_app.id,
            hostname="app.example.com", cert_arn="arn:a",
        )


@pytest.mark.django_db
def test_attach_raises_not_found_for_unknown_application(fixtures):
    infra, _app, _session = fixtures

    with pytest.raises(routing.CustomDomainNotFound):
        routing.attach_custom_domain(
            infrastructure_id=infra.id, application_id=uuid.uuid4(),
            hostname="app.example.com", cert_arn="arn:a",
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
            hostname="app.example.com", cert_arn="arn:a",
        )


@pytest.mark.django_db
def test_attach_raises_sni_cap_reached(fixtures, settings):
    settings.MAX_SNI_CERTIFICATES_PER_LISTENER = 1
    infra, app, _session = fixtures

    routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="a.example.com", cert_arn="arn:a",
    )

    with pytest.raises(routing.SniCapReached):
        routing.attach_custom_domain(
            infrastructure_id=infra.id, application_id=app.id,
            hostname="b.example.com", cert_arn="arn:b",
        )

    from api.models.custom_domain_route import CustomDomainRoute
    assert not CustomDomainRoute.objects.filter(hostname="b.example.com").exists()


@pytest.mark.django_db
def test_detach_removes_route_and_no_ops_when_missing(fixtures):
    infra, app, _session = fixtures
    routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="app.example.com", cert_arn="arn:a",
    )

    assert routing.detach_custom_domain("app.example.com") is True
    assert routing.detach_custom_domain("app.example.com") is False

    from api.models.custom_domain_route import CustomDomainRoute
    assert not CustomDomainRoute.objects.filter(hostname="app.example.com").exists()


@pytest.mark.django_db
def test_detach_removes_local_row_even_if_aws_side_fails(fixtures):
    infra, app, _session = fixtures
    routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="app.example.com", cert_arn="arn:a",
    )

    with patch("aws.session.create_boto3_session", side_effect=RuntimeError("boom")):
        assert routing.detach_custom_domain("app.example.com") is True

    from api.models.custom_domain_route import CustomDomainRoute
    assert not CustomDomainRoute.objects.filter(hostname="app.example.com").exists()


@pytest.mark.django_db
def test_detach_custom_domains_for_application_returns_hostnames(fixtures):
    infra, app, _session = fixtures
    routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="a.example.com", cert_arn="arn:a",
    )
    routing.attach_custom_domain(
        infrastructure_id=infra.id, application_id=app.id,
        hostname="b.example.com", cert_arn="arn:b",
    )

    hostnames = routing.detach_custom_domains_for_application(app.id)

    assert set(hostnames) == {"a.example.com", "b.example.com"}
    from api.models.custom_domain_route import CustomDomainRoute
    assert CustomDomainRoute.objects.filter(application_id=app.id).count() == 0


@pytest.mark.django_db
def test_notify_infrastructure_service_is_best_effort_on_failure():
    with patch("shared.resilience.http_client.ResilientHttpClient.post", side_effect=RuntimeError("down")):
        # Must not raise.
        routing.notify_infrastructure_service_of_deleted_application(uuid.uuid4(), uuid.uuid4())
