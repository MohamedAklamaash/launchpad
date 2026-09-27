"""api/common/host_url.py:app_host_url — the full gate the application detail API uses to
decide whether to show a live host URL (F1b part 3a).

Host URL shown only when: platform domain configured, dns_label minted, cert ISSUED, DNS
INSYNC, 443/Ingress-TLS applied, AND this specific app has a live host route."""
import uuid

import pytest
from shared.enums.orchestrator import ComputeType

from api.common.host_url import app_host_url


@pytest.fixture
def make_app(schema_db):
    from api.models.application import Application
    from api.models.infrastructure import Infrastructure
    from api.models.user import User

    def _make(compute_type=ComputeType.ECS_FARGATE, infra_overrides=None, app_overrides=None):
        user = User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")
        infra_defaults = {
            "dns_label": "0123456789abcdef", "tls_status": "ISSUED",
            "dns_synced": True, "https_ready": True,
        }
        infra_defaults.update(infra_overrides or {})
        infra = Infrastructure.objects.create(
            id=uuid.uuid4(), user=user, name=f"infra-{uuid.uuid4()}", cloud_provider="aws",
            max_cpu=1024, max_memory=512, compute_type=compute_type, **infra_defaults,
        )
        app_defaults = {
            "name": "my-app", "status": "ACTIVE",
            "host_forward_rule_arn": "arn:aws:elasticloadbalancing:::listener-rule/x",
        }
        app_defaults.update(app_overrides or {})
        return Application.objects.create(
            id=uuid.uuid4(), user=user, infrastructure=infra,
            project_remote_url="https://github.com/x/y", project_branch="main",
            project_commit_hash="", envs={}, port=8080,
            alloted_cpu=256, alloted_memory=512, attached_database_ids=[], **app_defaults,
        )
    return _make


@pytest.fixture(autouse=True)
def platform_domain(settings):
    settings.PLATFORM_BASE_DOMAIN = "launchpad.aklamaash.me"


def test_host_url_live_when_every_leg_is_satisfied(make_app):
    app = make_app()
    host_url, status = app_host_url(app)
    assert host_url == "https://my-app.0123456789abcdef.launchpad.aklamaash.me"
    assert status is None


def test_host_url_null_without_platform_domain(make_app, settings):
    settings.PLATFORM_BASE_DOMAIN = None
    app = make_app()
    assert app_host_url(app) == (None, "platform_domain_unconfigured")


def test_host_url_null_without_dns_label(make_app):
    app = make_app(infra_overrides={"dns_label": None})
    assert app_host_url(app) == (None, "dns_label_not_minted")


def test_host_url_null_when_tls_not_issued(make_app):
    app = make_app(infra_overrides={"tls_status": "PENDING"})
    assert app_host_url(app) == (None, "tls_not_issued")


def test_host_url_null_when_tls_failed(make_app):
    app = make_app(infra_overrides={"tls_status": "FAILED"})
    assert app_host_url(app) == (None, "tls_not_issued")


def test_host_url_null_when_dns_not_synced(make_app):
    app = make_app(infra_overrides={"dns_synced": False})
    assert app_host_url(app) == (None, "dns_not_synced")


def test_host_url_null_when_https_not_applied(make_app):
    app = make_app(infra_overrides={"https_ready": False})
    assert app_host_url(app) == (None, "https_listener_not_applied")


def test_host_url_null_when_app_has_no_host_forward_rule_yet(make_app):
    """Every infra-level leg is live, but THIS app hasn't been (re)deployed in host mode."""
    app = make_app(app_overrides={"host_forward_rule_arn": None})
    assert app_host_url(app) == (None, "host_route_not_applied")


def test_host_url_null_when_slug_is_not_hostname_safe(make_app):
    app = make_app(app_overrides={"name": "my.app"})
    assert app_host_url(app) == (None, "slug_not_hostname_safe")


# ── EKS uses host_route_applied, not host_forward_rule_arn ──────────────────────────────

def test_host_url_live_for_eks_when_host_route_applied(make_app):
    app = make_app(
        compute_type=ComputeType.EKS,
        app_overrides={"host_forward_rule_arn": None, "host_route_applied": True},
    )
    host_url, status = app_host_url(app)
    assert host_url == "https://my-app.0123456789abcdef.launchpad.aklamaash.me"
    assert status is None


def test_host_url_null_for_eks_when_host_route_not_applied(make_app):
    app = make_app(
        compute_type=ComputeType.EKS,
        app_overrides={"host_forward_rule_arn": "arn:not-relevant-for-eks", "host_route_applied": False},
    )
    assert app_host_url(app) == (None, "host_route_not_applied")


# ── R2: host_url requires the app to actually be ACTIVE right now ──────────────────────

@pytest.mark.parametrize("status", ["CREATED", "BUILDING", "PUSHING_IMAGE", "DEPLOYING", "SLEEPING", "FAILED"])
def test_host_url_null_when_application_is_not_active(make_app, status):
    """A stale host_forward_rule_arn from a previous successful deploy must not make
    app_host_url report a live URL while the app itself is mid-deploy, sleeping, or failed —
    every infra-level leg can be perfectly live and this must still be null."""
    app = make_app(app_overrides={"status": status})
    host_url, reason = app_host_url(app)
    assert host_url is None
    assert reason == f"application_not_active_{status.lower()}"


def test_host_url_live_when_application_is_active(make_app):
    app = make_app(app_overrides={"status": "ACTIVE"})
    host_url, reason = app_host_url(app)
    assert host_url is not None
    assert reason is None


# ── path URL is unaffected regardless ────────────────────────────────────────────────────

def test_deployment_url_field_is_independent_of_host_url_gating(make_app):
    """host_url is additive — deployment_url (the path URL) is a plain model field the view
    always returns, gated on nothing this module controls."""
    app = make_app(infra_overrides={"tls_status": "FAILED"})
    app.deployment_url = "http://alb.example.com/my-app"
    app.save()
    assert app.deployment_url == "http://alb.example.com/my-app"
    assert app_host_url(app) == (None, "tls_not_issued")
