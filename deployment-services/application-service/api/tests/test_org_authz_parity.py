"""F9 ticket 7, flag OFF: every converted site behaves exactly as it did before this
ticket. Legacy owner / InfrastructureUserRole / invited_users decide; Membership rows and
org claims are ignored. Statuses here are the pre-F9 ones."""
import uuid
from unittest.mock import patch

import pytest
from shared.enums.user_role import UserRole

from api.tests.org_authz_support import (
    add_member,
    call,
    call_class_view,
    make_db_user,
    make_org,
    make_stack,
    token_user,
)

pytestmark = pytest.mark.django_db

CALLERS = ["owner", "admin", "user", "guest", "stranger"]


@pytest.fixture
def world(orgs_off, schema_db):
    from api.models.infrastructure_user_role import InfrastructureUserRole

    owner = make_db_user()
    infra, app = make_stack(organization=None, owner=owner)
    people = {"owner": owner, "stranger": make_db_user()}
    for name, role in (("admin", UserRole.ADMIN), ("user", UserRole.USER), ("guest", UserRole.GUEST)):
        member = make_db_user()
        infra.invited_users.add(member)
        InfrastructureUserRole.objects.create(infrastructure=infra, user=member, role=role)
        people[name] = member
    return infra, app, people


def _sites():
    from api.views import (
        app_metrics,
        application,
        build_logs,
        custom_domains_internal,
        exit_inventory,
        infrastructure_validation,
        runtime_logs,
    )

    return {
        "detail": lambda f, u, app, infra: call_class_view(f, application.ApplicationDetailDeleteView, u, "get", pk=app.id),
        "deploy": lambda f, u, app, infra: call_class_view(f, application.ApplicationDeployView, u, "post", pk=app.id),
        "retry": lambda f, u, app, infra: call_class_view(f, application.ApplicationRetryDeployView, u, "post", pk=app.id),
        "update": lambda f, u, app, infra: call_class_view(
            f, application.ApplicationUpdateView, u, "patch", data={"description": "d"}, pk=app.id),
        "update_envs": lambda f, u, app, infra: call_class_view(
            f, application.ApplicationUpdateView, u, "patch", data={"envs": {"K": "v"}}, pk=app.id),
        "attach_db": lambda f, u, app, infra: call_class_view(
            f, application.ApplicationUpdateView, u, "patch", data={"attached_database_ids": []}, pk=app.id),
        "delete": lambda f, u, app, infra: call_class_view(f, application.ApplicationDetailDeleteView, u, "delete", pk=app.id),
        "rollback_list": lambda f, u, app, infra: call_class_view(f, application.ApplicationDeploymentsView, u, "get", pk=app.id),
        "resume": lambda f, u, app, infra: call_class_view(f, application.ApplicationResumeAutoDeployView, u, "post", pk=app.id),
        "sleep": lambda f, u, app, infra: call_class_view(f, application.ApplicationSleepView, u, "post", pk=app.id),
        "wake": lambda f, u, app, infra: call_class_view(f, application.ApplicationWakeView, u, "post", pk=app.id),
        "rotate": lambda f, u, app, infra: call(f, application.application_rotate_webhook_secret, u, method="post", app_id=app.id),
        "logs": lambda f, u, app, infra: call(f, runtime_logs.runtime_logs, u, app_id=app.id),
        "logs_stream": lambda f, u, app, infra: call(f, runtime_logs.runtime_logs_stream, u, app_id=app.id),
        "build_logs": lambda f, u, app, infra: call(f, build_logs.current_build_logs, u, app_id=app.id),
        "metrics": lambda f, u, app, infra: call(f, app_metrics.application_metrics, u, app_id=app.id),
        "inventory": lambda f, u, app, infra: call(f, exit_inventory.export_inventory, u, infra_id=infra.id),
        "validation": lambda f, u, app, infra: call(f, infrastructure_validation.infrastructure_validation, u, infra_id=infra.id),
        "summary": lambda f, u, app, infra: call(
            f, custom_domains_internal.application_summary_for_custom_domains, u, app_id=app.id),
    }


# owner, admin, user, guest, stranger
EXPECTED = {
    "detail": (200, 200, 200, 200, 404),
    "deploy": (202, 202, 403, 403, 403),
    "retry": (202, 202, 403, 403, 403),
    "update": (200, 200, 403, 403, 403),
    "update_envs": (200, 200, 403, 403, 403),
    "attach_db": (200, 200, 403, 403, 403),
    "delete": (204, 204, 403, 403, 403),
    "rollback_list": (200, 403, 403, 403, 404),
    "resume": (200, 403, 403, 403, 404),
    "sleep": (200, 403, 403, 403, 403),
    "wake": (200, 403, 403, 403, 403),
    "rotate": (200, 403, 403, 403, 403),
    "logs": (409, 403, 403, 403, 404),
    "logs_stream": (409, 403, 403, 403, 404),
    "build_logs": (404, 403, 403, 403, 404),
    "metrics": (409, 403, 403, 403, 404),
    "inventory": (200, 403, 403, 403, 404),
    "validation": (200, 200, 200, 200, 404),
    "summary": (200, 200, 200, 200, 404),
}


@pytest.mark.parametrize("who", CALLERS)
@pytest.mark.parametrize("name", list(EXPECTED))
def test_legacy_status_for_every_converted_site(factory, world, settings, name, who):
    infra, app, people = world
    settings.PUBLIC_GATEWAY_URL = "https://gateway.example.com"
    caller = people[who]
    with patch("api.views.application.ApplicationSleepService.sleep_application"), \
         patch("api.views.application.ApplicationSleepService.wake_application"), \
         patch("api.views.exit_inventory.export_data_for_infrastructure", return_value={"apps": []}):
        resp = _sites()[name](factory, caller, app, infra)
    assert resp.status_code == EXPECTED[name][CALLERS.index(who)]


def test_owner_still_sees_env_values_and_so_do_guests(factory, world):
    infra, app, people = world
    for who in ("owner", "guest"):
        resp = _sites()["detail"](factory, people[who], app, infra)
        assert resp.data["envs"] == {"API_KEY": "placeholder-value"}


CREATE_BODY = {
    "name": "fresh", "project_remote_url": "https://github.com/o/r", "project_branch": "main",
    "alloted_cpu": 0.25, "alloted_memory": 0.5,
}


@pytest.mark.parametrize("who,expected", [("owner", 201), ("admin", 201), ("user", 403), ("guest", 403), ("stranger", 403)])
def test_create_application_legacy_roles(factory, world, who, expected):
    from api.views.application import ApplicationListCreateView

    infra, _, people = world
    body = {**CREATE_BODY, "infrastructure_id": str(infra.id)}
    resp = call_class_view(factory, ApplicationListCreateView, people[who], "post", data=body)
    assert resp.status_code == expected


def test_create_stamps_the_infra_org_and_ignores_a_client_supplied_one(factory, orgs_off, schema_db):
    from api.models.application import Application
    from api.views.application import ApplicationListCreateView

    infra, _ = make_stack(organization=make_org())
    body = {**CREATE_BODY, "infrastructure_id": str(infra.id), "organization_id": str(uuid.uuid4())}
    resp = call_class_view(factory, ApplicationListCreateView, infra.user, "post", data=body)
    assert resp.status_code == 201
    assert Application.objects.get(id=resp.data["id"]).organization_id == infra.organization_id


def test_list_applications_legacy_membership(factory, world):
    from rest_framework.test import force_authenticate

    from api.views.application import ApplicationListCreateView

    infra, app, people = world
    for who, visible in (("owner", True), ("guest", True), ("stranger", False)):
        request = factory.get("/x/", {"infrastructure_id": str(infra.id)})
        force_authenticate(request, user=people[who])
        data = ApplicationListCreateView.as_view()(request).data
        assert ([a["id"] for a in data] == [str(app.id)]) is visible


def test_membership_rows_and_org_claims_are_ignored(factory, world):
    infra, app, people = world
    org = make_org()
    account = add_member(org, "owner")
    outsider = token_user(org=org, account_id=account, sub=people["stranger"].id)
    assert _sites()["detail"](factory, outsider, app, infra).status_code == 404


def test_null_org_rows_remain_reachable_by_their_legacy_owner(factory, world):
    infra, app, people = world
    assert infra.organization_id is None and app.organization_id is None
    assert _sites()["detail"](factory, people["owner"], app, infra).status_code == 200
