"""F9 ticket 7: application-service view authorization under ORGS_ENABLED, driven at the
HTTP view layer. Every matrix cell that has a site in this service, for all four roles,
plus cross-org / tombstone / NULL-org / org-disagreement / legacy-grant failure paths.
Role resolution runs against real Membership rows; only the Redis role cache, the F0
budget, the deploy queue and the deploy lock are stubbed."""
import hashlib
import hmac
import json
import logging
import uuid
from unittest.mock import patch

import pytest
from shared.authz.org_matrix import MATRIX, Capability

from api.tests.org_authz_support import (
    ROLES,
    add_member,
    call,
    call_class_view,
    make_db_user,
    make_org,
    make_stack,
    token_user,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def org(orgs_on, schema_db):
    return make_org()


def _as(org, role, **kw):
    return token_user(org=org, account_id=add_member(org, role), **kw)


def _allowed(role, capability):
    return role in MATRIX[capability]


# ── VIEW ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ROLES)
def test_application_detail_is_visible_to_every_role(factory, org, role):
    from api.views.application import ApplicationDetailDeleteView

    _, app = make_stack(organization=org)
    resp = call_class_view(factory, ApplicationDetailDeleteView, _as(org, role), "get", pk=app.id)
    assert resp.status_code == 200
    assert resp.data["id"] == str(app.id)


@pytest.mark.parametrize("role", ROLES)
def test_env_values_are_withheld_from_roles_without_env_access(factory, org, role):
    from api.views.application import ApplicationDetailDeleteView

    _, app = make_stack(organization=org)
    resp = call_class_view(factory, ApplicationDetailDeleteView, _as(org, role), "get", pk=app.id)
    if _allowed(role, Capability.ENV_VARS):
        assert resp.data["envs"] == {"API_KEY": "placeholder-value"}
    else:
        assert resp.data["envs"] == {}
        assert "placeholder-value" not in json.dumps(resp.data)


@pytest.mark.parametrize("role", ROLES)
def test_application_list_is_scoped_to_the_infra_org(factory, org, role):
    from api.views.application import ApplicationListCreateView

    infra, app = make_stack(organization=org)
    _, other_app = make_stack(organization=make_org())
    stray = make_stack(organization=org, app_organization=make_org())[1]
    stray.infrastructure_id = infra.id
    stray.name = "stray"
    stray.save()
    resp = call_class_view(factory, ApplicationListCreateView, _as(org, role), "get")
    assert resp.status_code == 400  # infrastructure_id is required
    request_user = _as(org, role)
    from rest_framework.test import force_authenticate
    request = factory.get("/x/", {"infrastructure_id": str(infra.id)})
    force_authenticate(request, user=request_user)
    resp = ApplicationListCreateView.as_view()(request)
    assert [a["id"] for a in resp.data] == [str(app.id)]
    assert str(other_app.id) not in json.dumps(resp.data)


@pytest.mark.parametrize("role", ROLES)
def test_infrastructure_validation_is_a_view_level_read(factory, org, role):
    from api.views.infrastructure_validation import infrastructure_validation

    infra, _ = make_stack(organization=org)
    assert call(factory, infrastructure_validation, _as(org, role), infra_id=infra.id).status_code == 200


# ── DEPLOY: create, update, deploy, retry, sleep, wake, webhook secret ───────────────

@pytest.mark.parametrize("role", ROLES)
def test_deploy_and_retry_matrix(factory, org, role):
    from api.views.application import ApplicationDeployView, ApplicationRetryDeployView

    _, app = make_stack(organization=org)
    for view_class in (ApplicationDeployView, ApplicationRetryDeployView):
        resp = call_class_view(factory, view_class, _as(org, role), "post", pk=app.id)
        assert resp.status_code == (202 if _allowed(role, Capability.DEPLOY) else 403)


@pytest.mark.parametrize("role", ROLES)
def test_sleep_and_wake_matrix(factory, org, role):
    from api.views.application import ApplicationSleepView, ApplicationWakeView

    _, app = make_stack(organization=org)
    with patch("api.views.application.ApplicationSleepService.sleep_application"), \
         patch("api.views.application.ApplicationSleepService.wake_application"):
        for view_class in (ApplicationSleepView, ApplicationWakeView):
            resp = call_class_view(factory, view_class, _as(org, role), "post", pk=app.id)
            assert resp.status_code == (200 if _allowed(role, Capability.DEPLOY) else 403)


@pytest.mark.parametrize("role", ROLES)
def test_sleep_by_a_member_who_did_not_create_the_app(factory, org, role):
    """The legacy gate compared app.user_id to the caller. The org gate does not."""
    from api.views.application import ApplicationSleepView

    _, app = make_stack(organization=org, creator=make_db_user())
    user = _as(org, role, sub=uuid.uuid4())
    with patch("api.views.application.ApplicationSleepService.sleep_application"):
        resp = call_class_view(factory, ApplicationSleepView, user, "post", pk=app.id)
    assert resp.status_code == (200 if _allowed(role, Capability.DEPLOY) else 403)


@pytest.mark.parametrize("role", ROLES)
def test_rotate_webhook_secret_matrix(factory, org, role, settings):
    from api.views.application import application_rotate_webhook_secret

    settings.PUBLIC_GATEWAY_URL = "https://gateway.example.com"
    _, app = make_stack(organization=org)
    resp = call(factory, application_rotate_webhook_secret, _as(org, role), method="post", app_id=app.id)
    assert resp.status_code == (200 if _allowed(role, Capability.DEPLOY) else 403)


@pytest.mark.parametrize("role", ROLES)
def test_update_config_matrix(factory, org, role):
    from api.views.application import ApplicationUpdateView

    _, app = make_stack(organization=org)
    resp = call_class_view(factory, ApplicationUpdateView, _as(org, role), "patch",
                           data={"description": "new text"}, pk=app.id)
    assert resp.status_code == (200 if _allowed(role, Capability.DEPLOY) else 403)


@pytest.mark.parametrize("role", ROLES)
def test_update_env_vars_matrix(factory, org, role):
    from api.views.application import ApplicationUpdateView

    _, app = make_stack(organization=org)
    resp = call_class_view(factory, ApplicationUpdateView, _as(org, role), "patch",
                           data={"envs": {"NEW_KEY": "v"}}, pk=app.id)
    assert resp.status_code == (200 if _allowed(role, Capability.ENV_VARS) else 403)
    app.refresh_from_db()
    assert ("NEW_KEY" in app.envs) is _allowed(role, Capability.ENV_VARS)


@pytest.mark.parametrize("role", ROLES)
def test_attach_database_matrix(factory, org, role):
    from api.views.application import ApplicationUpdateView

    _, app = make_stack(organization=org)
    resp = call_class_view(factory, ApplicationUpdateView, _as(org, role), "patch",
                           data={"attached_database_ids": []}, pk=app.id)
    assert resp.status_code == (200 if _allowed(role, Capability.DATABASES) else 403)


CREATE_BODY = {
    "name": "fresh", "project_remote_url": "https://github.com/o/r", "project_branch": "main",
    "alloted_cpu": 0.25, "alloted_memory": 0.5,
}


@pytest.mark.parametrize("role", ROLES)
def test_create_application_matrix_and_stamps_infra_org(factory, org, role):
    from api.models.application import Application
    from api.views.application import ApplicationListCreateView

    infra, _ = make_stack(organization=org)
    body = {**CREATE_BODY, "infrastructure_id": str(infra.id), "organization_id": str(uuid.uuid4())}
    resp = call_class_view(factory, ApplicationListCreateView, _as(org, role), "post", data=body)
    if _allowed(role, Capability.DEPLOY):
        assert resp.status_code == 201, resp.data
        assert str(Application.objects.get(id=resp.data["id"]).organization_id) == str(org.id)
    else:
        assert resp.status_code == 403


def test_create_application_in_another_orgs_infra_looks_like_a_missing_infra(factory, org):
    from api.views.application import ApplicationListCreateView

    infra, _ = make_stack(organization=make_org())
    body = {**CREATE_BODY, "infrastructure_id": str(infra.id)}
    resp = call_class_view(factory, ApplicationListCreateView, _as(org, "owner"), "post", data=body)
    assert resp.status_code == 400
    assert resp.data["error"] == "Infrastructure not found"


# ── DELETE_APPLICATION (carry-forward owner+admin) ───────────────────────────────────

@pytest.mark.parametrize("role", ROLES)
def test_delete_application_matrix(factory, org, role):
    from api.models.application import Application
    from api.views.application import ApplicationDetailDeleteView

    _, app = make_stack(organization=org)
    resp = call_class_view(factory, ApplicationDetailDeleteView, _as(org, role), "delete", pk=app.id)
    if _allowed(role, Capability.DELETE_APPLICATION):
        assert resp.status_code == 204
        assert not Application.objects.filter(id=app.id).exists()
    else:
        assert resp.status_code == 403
        assert Application.objects.filter(id=app.id).exists()


# ── ROLLBACK ─────────────────────────────────────────────────────────────────────────

@pytest.fixture
def deployment(org):
    from api.models.deployment import Deployment

    def _make(app):
        return Deployment.objects.create(
            application=app, image_tag="app-" + "b" * 40, commit_sha="b" * 40,
            tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA, compute_type="ecs_fargate",
            env_keys=["API_KEY"], env_values_hash="0" * 64, attached_database_ids=[],
            cpu=128, memory=256, port=3000,
            status=Deployment.STATUS_SUCCEEDED, triggered_by=Deployment.TRIGGERED_BY_DEPLOY,
        )
    return _make


@pytest.mark.parametrize("role", ROLES)
def test_rollback_family_matrix(factory, org, role, deployment):
    from api.views.application import (
        ApplicationDeploymentsView,
        ApplicationResumeAutoDeployView,
        ApplicationRollbackPreviewView,
        ApplicationRollbackView,
    )

    _, app = make_stack(organization=org)
    target = deployment(app)
    expected = 200 if _allowed(role, Capability.ROLLBACK) else 403
    user = _as(org, role)
    assert call_class_view(factory, ApplicationDeploymentsView, user, "get", pk=app.id).status_code == expected
    assert call_class_view(factory, ApplicationRollbackPreviewView, user, "get",
                           pk=app.id, deployment_id=target.id).status_code == expected
    assert call_class_view(factory, ApplicationResumeAutoDeployView, user, "post", pk=app.id).status_code == expected
    rolled = call_class_view(factory, ApplicationRollbackView, user, "post", pk=app.id, deployment_id=target.id)
    assert rolled.status_code == (202 if _allowed(role, Capability.ROLLBACK) else 403)


# ── LOGS_METRICS: runtime logs, SSE, build logs, metrics ─────────────────────────────

@pytest.mark.parametrize("role", ROLES)
def test_runtime_logs_and_stream_matrix(factory, org, role):
    from api.views.runtime_logs import runtime_logs, runtime_logs_stream

    _, app = make_stack(organization=org, env_status="PENDING")
    for view in (runtime_logs, runtime_logs_stream):
        resp = call(factory, view, _as(org, role), app_id=app.id)
        assert resp.status_code == (409 if _allowed(role, Capability.LOGS_METRICS) else 403)


@pytest.mark.parametrize("role", ROLES)
def test_build_logs_and_stream_matrix(factory, org, role):
    from api.views.build_logs import (
        build_logs,
        build_logs_stream,
        current_build_logs,
        current_build_logs_stream,
    )

    _, app = make_stack(organization=org)
    for view, kwargs in (
        (current_build_logs, {}), (current_build_logs_stream, {}),
        (build_logs, {"deployment_id": uuid.uuid4()}), (build_logs_stream, {"deployment_id": uuid.uuid4()}),
    ):
        resp = call(factory, view, _as(org, role), app_id=app.id, **kwargs)
        if _allowed(role, Capability.LOGS_METRICS):
            assert resp.status_code == 404
            assert "build" in resp.data["error"].lower() or "deployment" in resp.data["error"].lower()
        else:
            assert resp.status_code == 403


@pytest.mark.parametrize("role", ROLES)
def test_metrics_matrix(factory, org, role):
    from api.views.app_metrics import application_metrics

    _, app = make_stack(organization=org, env_status="PENDING")
    resp = call(factory, application_metrics, _as(org, role), app_id=app.id)
    assert resp.status_code == (409 if _allowed(role, Capability.LOGS_METRICS) else 403)


# ── EXIT inventory and the custom-domain application lookup ──────────────────────────

@pytest.mark.parametrize("role", ROLES)
def test_export_inventory_matrix(factory, org, role):
    from api.views.exit_inventory import export_inventory

    infra, _ = make_stack(organization=org)
    with patch("api.views.exit_inventory.export_data_for_infrastructure", return_value={"apps": []}):
        resp = call(factory, export_inventory, _as(org, role), infra_id=infra.id)
    assert resp.status_code == (200 if _allowed(role, Capability.EXIT) else 403)


@pytest.mark.parametrize("role", ROLES)
def test_application_summary_for_custom_domains_matrix(factory, org, role):
    from api.views.custom_domains_internal import application_summary_for_custom_domains

    _, app = make_stack(organization=org)
    resp = call(factory, application_summary_for_custom_domains, _as(org, role), app_id=app.id)
    assert resp.status_code == (200 if _allowed(role, Capability.CUSTOM_DOMAINS) else 403)


# ── failure paths that apply to every converted site ─────────────────────────────────

def _detail(factory, user, app, infra):
    from api.views.application import ApplicationDetailDeleteView
    return call_class_view(factory, ApplicationDetailDeleteView, user, "get", pk=app.id)


def _deploy(factory, user, app, infra):
    from api.views.application import ApplicationDeployView
    return call_class_view(factory, ApplicationDeployView, user, "post", pk=app.id)


def _logs(factory, user, app, infra):
    from api.views.runtime_logs import runtime_logs
    return call(factory, runtime_logs, user, app_id=app.id)


def _rollback_list(factory, user, app, infra):
    from api.views.application import ApplicationDeploymentsView
    return call_class_view(factory, ApplicationDeploymentsView, user, "get", pk=app.id)


def _metrics(factory, user, app, infra):
    from api.views.app_metrics import application_metrics
    return call(factory, application_metrics, user, app_id=app.id)


def _validation(factory, user, app, infra):
    from api.views.infrastructure_validation import infrastructure_validation
    return call(factory, infrastructure_validation, user, infra_id=infra.id)


def _inventory(factory, user, app, infra):
    from api.views.exit_inventory import export_inventory
    return call(factory, export_inventory, user, infra_id=infra.id)


SITES = [_detail, _deploy, _logs, _rollback_list, _metrics, _validation, _inventory]


@pytest.mark.parametrize("site", SITES, ids=[s.__name__ for s in SITES])
def test_resource_in_another_org_is_404_even_for_an_owner_there(factory, org, site):
    from api.models.membership import Membership

    other = make_org()
    infra, app = make_stack(organization=other)
    account = add_member(org, "owner")
    Membership.objects.create(organization=other, account_id=account, role="owner", version=1)
    assert site(factory, token_user(org=org, account_id=account), app, infra).status_code == 404


@pytest.mark.parametrize("site", SITES, ids=[s.__name__ for s in SITES])
def test_removed_member_is_404(factory, org, site):
    infra, app = make_stack(organization=org)
    gone = token_user(org=org, account_id=add_member(org, "owner", removed=True))
    assert site(factory, gone, app, infra).status_code == 404


@pytest.mark.parametrize("site", SITES, ids=[s.__name__ for s in SITES])
def test_null_org_infra_is_inaccessible_and_logged(factory, org, site, caplog):
    infra, app = make_stack(organization=None)
    owner = token_user(org=org, account_id=add_member(org, "owner"))
    with caplog.at_level(logging.WARNING):
        assert site(factory, owner, app, infra).status_code == 404
    assert "no organization_id" in caplog.text


@pytest.mark.parametrize("site", [s for s in SITES if s not in (_validation, _inventory)],
                         ids=[s.__name__ for s in SITES if s not in (_validation, _inventory)])
def test_application_whose_org_disagrees_with_its_infra_is_404_and_logged(factory, org, site, caplog):
    infra, app = make_stack(organization=org, app_organization=make_org())
    owner = token_user(org=org, account_id=add_member(org, "owner"))
    with caplog.at_level(logging.ERROR):
        assert site(factory, owner, app, infra).status_code == 404
    assert "disagrees" in caplog.text


@pytest.mark.parametrize("site", [s for s in SITES if s not in (_validation, _inventory)],
                         ids=[s.__name__ for s in SITES if s not in (_validation, _inventory)])
def test_application_with_null_org_under_an_org_infra_is_404(factory, org, site):
    infra, app = make_stack(organization=org, app_organization=None)
    owner = token_user(org=org, account_id=add_member(org, "owner"))
    assert site(factory, owner, app, infra).status_code == 404


@pytest.mark.parametrize("site", SITES, ids=[s.__name__ for s in SITES])
def test_legacy_invited_user_and_role_edge_grant_nothing(factory, org, site):
    from shared.enums.user_role import UserRole

    from api.models.infrastructure_user_role import InfrastructureUserRole

    infra, app = make_stack(organization=org)
    legacy_member = make_db_user()
    infra.invited_users.add(legacy_member)
    InfrastructureUserRole.objects.create(infrastructure=infra, user=legacy_member, role=UserRole.ADMIN)
    token = token_user(org=org, account_id=uuid.uuid4(), sub=legacy_member.id)
    assert site(factory, token, app, infra).status_code == 404


@pytest.mark.parametrize("site", SITES, ids=[s.__name__ for s in SITES])
def test_legacy_owner_who_is_not_a_member_loses_access(factory, org, site):
    infra, app = make_stack(organization=org)
    token = token_user(org=org, account_id=uuid.uuid4(), sub=infra.user_id)
    assert site(factory, token, app, infra).status_code == 404


def test_token_without_account_id_resolves_no_role(factory, org):
    infra, app = make_stack(organization=org)
    assert _detail(factory, token_user(org=org, account_id=None), app, infra).status_code == 404


def test_malformed_application_id_is_404_not_500(factory, org):
    from api.views.runtime_logs import runtime_logs

    owner = token_user(org=org, account_id=add_member(org, "owner"))
    assert call(factory, runtime_logs, owner, app_id="not-a-uuid").status_code == 404


def test_flag_on_without_actor_fails_closed(org):
    from api.services.application_service import ApplicationService

    _, app = make_stack(organization=org)
    assert ApplicationService().get_application_details(app.user_id, app.id) is None


# ── the GitHub webhook stays HMAC-authenticated and ignores org roles ────────────────

def _signed(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_needs_a_valid_signature_not_an_org_role(factory, org):
    from api.views.application import application_github_webhook

    _, app = make_stack(organization=org)
    hook_key = uuid.uuid4().hex
    app.github_webhook_secret = hook_key
    app.save()
    body = json.dumps({"ref": "refs/heads/main", "after": "c" * 40}).encode()

    def post(signature):
        request = factory.post("/x/", data=body, content_type="application/json",
                               HTTP_X_GITHUB_EVENT="push", HTTP_X_HUB_SIGNATURE_256=signature)
        return application_github_webhook(request, app_id=str(app.id))

    assert post("sha256=" + "0" * 64).status_code == 401
    assert post(_signed(hook_key, body)).status_code == 202


# ── mirror propagation ───────────────────────────────────────────────────────────────

def test_infra_mirror_upsert_stores_organization_id_write_once(org):
    from api.models.infrastructure import Infrastructure
    from api.repositories.infrastructure import InfrastructureRepository

    owner = make_db_user()
    infra_id = uuid.uuid4()
    payload = {"id": infra_id, "user_id": owner.id, "name": "m", "max_cpu": 1, "max_memory": 1,
               "organization_id": str(org.id)}
    repo = InfrastructureRepository()
    repo.upsert_infrastructure(payload)
    assert str(Infrastructure.objects.get(id=infra_id).organization_id) == str(org.id)

    repo.upsert_infrastructure({**payload, "organization_id": str(uuid.uuid4())})
    assert str(Infrastructure.objects.get(id=infra_id).organization_id) == str(org.id)

    repo.upsert_infrastructure({k: v for k, v in payload.items() if k != "organization_id"})
    assert str(Infrastructure.objects.get(id=infra_id).organization_id) == str(org.id)


def test_infra_mirror_fills_a_null_organization_id_and_rejects_garbage(org):
    from api.models.infrastructure import Infrastructure
    from api.repositories.infrastructure import InfrastructureRepository

    infra, _ = make_stack(organization=None)
    base = {"id": infra.id, "user_id": infra.user_id, "name": infra.name, "max_cpu": 64, "max_memory": 128}
    repo = InfrastructureRepository()
    repo.upsert_infrastructure({**base, "organization_id": "not-a-uuid"})
    assert Infrastructure.objects.get(id=infra.id).organization_id is None
    repo.upsert_infrastructure({**base, "organization_id": str(org.id)})
    assert str(Infrastructure.objects.get(id=infra.id).organization_id) == str(org.id)
