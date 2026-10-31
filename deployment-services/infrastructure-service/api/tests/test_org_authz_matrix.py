"""F9 ticket 7: infrastructure-service view authorization under ORGS_ENABLED, driven at the
HTTP view layer. Every cell of plan/F9-teams-and-orgs.md's permission matrix that has a
site in this service, for all four roles, plus the cross-org / tombstone / NULL-org /
legacy-M2M failure paths. Role resolution runs against real Membership rows; only the
Redis role cache and the F0 budget are stubbed."""
import logging
import time
import uuid
from unittest.mock import patch

import pytest
from api.tests.org_authz_support import (
    ROLES,
    add_member,
    call,
    make_db_user,
    make_infra,
    make_org,
    token_user,
)
from shared.authz.org_matrix import MATRIX, PLAN_ROWS, Capability

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_infra_queue(monkeypatch):
    from unittest.mock import MagicMock

    fake = MagicMock()
    monkeypatch.setattr("api.services.nuke_service.InfraQueue", fake)
    monkeypatch.setattr("api.services.infrastructure.InfraQueue", fake)
    monkeypatch.setattr("api.services.database_service.InfraQueue", fake)


@pytest.fixture
def org(orgs_on):
    return make_org()


def _as(org, role, **kw):
    return token_user(org=org, account_id=add_member(org, role), **kw)


def _allowed(role, capability):
    return role in MATRIX[capability]


# ── the matrix as data matches the plan table, cell for cell ─────────────────────────

PLAN_TABLE = {
    Capability.VIEW: "yes yes yes yes",
    Capability.CREATE_INFRASTRUCTURE: "yes yes no no",
    Capability.DELETE_INFRASTRUCTURE: "yes no no no",
    Capability.DEPLOY: "yes yes yes no",
    Capability.ROLLBACK: "yes yes yes no",
    Capability.LOGS_METRICS: "yes yes yes no",
    Capability.COSTS: "yes yes no no",
    Capability.ENV_VARS: "yes yes yes no",
    Capability.DATABASES: "yes yes no no",
    Capability.CUSTOM_DOMAINS: "yes yes no no",
    Capability.EXIT: "yes no no no",
    Capability.MEMBERS_INVITE: "yes yes no no",
    Capability.MEMBERS_REMOVE: "yes yes no no",
    Capability.MEMBERS_CHANGE_ROLE: "yes yes no no",
    Capability.BILLING: "yes no no no",
}


def test_matrix_has_exactly_the_fifteen_plan_rows_in_order():
    assert list(PLAN_ROWS) == list(PLAN_TABLE)


@pytest.mark.parametrize("capability", list(PLAN_TABLE))
@pytest.mark.parametrize("role", ROLES)
def test_matrix_cell_matches_plan_table(capability, role):
    expected = PLAN_TABLE[capability].split()[ROLES.index(role)] == "yes"
    assert (role in MATRIX[capability]) is expected


def test_carry_forward_capabilities_keep_pre_f9_role_sets():
    assert MATRIX[Capability.DELETE_APPLICATION] == {"owner", "admin"}
    assert MATRIX[Capability.MANAGE_INFRASTRUCTURE] == {"owner"}


# ── VIEW ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ROLES)
def test_view_detail_allowed_for_every_role(factory, org, role):
    from api.views.infrastructure import infrastructure_detail

    infra = make_infra(organization=org)
    resp = call(factory, infrastructure_detail, _as(org, role), infra_id=infra.id)
    assert resp.status_code == 200
    assert resp.data["id"] == str(infra.id)


@pytest.mark.parametrize("role", ROLES)
def test_list_returns_only_token_org_infras(factory, org, role):
    from api.views.infrastructure import infrastructure_list_create

    other = make_org()
    mine = make_infra(organization=org)
    make_infra(organization=other)
    make_infra(organization=None)
    resp = call(factory, infrastructure_list_create, _as(org, role))
    assert resp.status_code == 200
    assert [i["id"] for i in resp.data] == [str(mine.id)]


def test_list_is_empty_for_a_caller_with_no_membership(factory, org):
    from api.views.infrastructure import infrastructure_list_create

    make_infra(organization=org)
    stranger = token_user(org=org, account_id=uuid.uuid4())
    assert call(factory, infrastructure_list_create, stranger).data == []


# ── CREATE ───────────────────────────────────────────────────────────────────────────

CREATE_BODY = {"name": "new-infra", "cloud_provider": "aws", "code": "210987654321", "max_cpu": 4, "max_memory": 8}


@pytest.mark.parametrize("role", ROLES)
def test_create_infrastructure_matrix_and_stamps_token_org(factory, org, role):
    from api.models.infrastructure import Infrastructure
    from api.views.infrastructure import infrastructure_list_create

    creator = make_db_user(role="user")
    user = token_user(org=org, account_id=add_member(org, role), sub=creator.id)
    resp = call(factory, infrastructure_list_create, user, method="post", data=CREATE_BODY)
    if _allowed(role, Capability.CREATE_INFRASTRUCTURE):
        assert resp.status_code == 201
        assert str(Infrastructure.objects.get(id=resp.data["id"]).organization_id) == str(org.id)
    else:
        assert resp.status_code == 403
        assert not Infrastructure.objects.filter(name="new-infra").exists()


def test_create_ignores_a_client_supplied_organization_id(factory, org):
    from api.models.infrastructure import Infrastructure
    from api.views.infrastructure import infrastructure_list_create

    elsewhere = make_org()
    creator = make_db_user()
    user = token_user(org=org, account_id=add_member(org, "owner"), sub=creator.id)
    body = {**CREATE_BODY, "organization_id": str(elsewhere.id)}
    resp = call(factory, infrastructure_list_create, user, method="post", data=body)
    assert resp.status_code == 201
    assert str(Infrastructure.objects.get(id=resp.data["id"]).organization_id) == str(org.id)


def test_create_with_no_membership_in_token_org_is_denied(factory, org):
    from api.views.infrastructure import infrastructure_list_create

    user = token_user(org=org, account_id=uuid.uuid4(), sub=make_db_user().id)
    assert call(factory, infrastructure_list_create, user, method="post", data=CREATE_BODY).status_code == 403


# ── DELETE_INFRASTRUCTURE (delete and nuke) ──────────────────────────────────────────

@pytest.mark.parametrize("role", ROLES)
def test_nuke_start_matrix(factory, org, role):
    from api.views.nuke import infrastructure_nuke

    infra = make_infra(organization=org, name="prod")
    resp = call(factory, infrastructure_nuke, _as(org, role), method="post",
                data={"confirm_name": "wrong"}, infra_id=infra.id)
    if _allowed(role, Capability.DELETE_INFRASTRUCTURE):
        assert resp.status_code == 409
        assert "confirm_name" in resp.data["error"]
    else:
        assert resp.status_code == 403


@pytest.mark.parametrize("role", ROLES)
def test_delete_infrastructure_matrix(factory, org, role):
    from api.models.infrastructure import Infrastructure
    from api.views.infrastructure import infrastructure_detail

    infra = make_infra(organization=org, status="PENDING")
    resp = call(factory, infrastructure_detail, _as(org, role), method="delete", infra_id=infra.id)
    if _allowed(role, Capability.DELETE_INFRASTRUCTURE):
        assert resp.status_code == 204
        assert not Infrastructure.objects.filter(id=infra.id).exists()
    else:
        assert resp.status_code == 403
        assert Infrastructure.objects.filter(id=infra.id).exists()


@pytest.mark.parametrize("role", ROLES)
def test_nuke_status_matrix(factory, org, role):
    from api.models.nuke_run import NukeRun, initial_steps
    from api.views.nuke import infrastructure_nuke

    infra = make_infra(organization=org)
    NukeRun.objects.create(
        infrastructure_id=infra.id, infrastructure_name=infra.name, user_id=infra.user_id,
        confirm_name=infra.name, status="PENDING", steps=initial_steps(),
    )
    resp = call(factory, infrastructure_nuke, _as(org, role), infra_id=infra.id)
    assert resp.status_code == (200 if _allowed(role, Capability.DELETE_INFRASTRUCTURE) else 403)


# ── COSTS ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ROLES)
def test_costs_matrix(factory, org, role):
    from api.views.costs import infrastructure_costs

    infra = make_infra(organization=org)
    with patch("api.services.cost_service.get_infrastructure_costs", return_value={"ok": True}):
        resp = call(factory, infrastructure_costs, _as(org, role), infra_id=infra.id)
    assert resp.status_code == (200 if _allowed(role, Capability.COSTS) else 403)


# ── DATABASES ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ROLES)
def test_database_create_matrix(factory, org, role):
    from api.views.database import database_list_create

    infra = make_infra(organization=org, status="PENDING")
    resp = call(factory, database_list_create, _as(org, role), method="post", data={"name": "db"}, infra_id=infra.id)
    if _allowed(role, Capability.DATABASES):
        assert resp.status_code == 400
        assert "ACTIVE" in resp.data["error"]
    else:
        assert resp.status_code == 403


@pytest.mark.parametrize("role", ROLES)
def test_database_delete_matrix(factory, org, role):
    from api.views.database import database_detail

    infra = make_infra(organization=org)
    resp = call(factory, database_detail, _as(org, role), method="delete", data={"confirm_name": "x"},
                infra_id=infra.id, database_id=uuid.uuid4())
    if _allowed(role, Capability.DATABASES):
        assert resp.status_code == 404
    else:
        assert resp.status_code == 403


@pytest.mark.parametrize("role", ROLES)
def test_database_list_is_a_view_level_read(factory, org, role):
    from api.views.database import database_list_create

    infra = make_infra(organization=org)
    resp = call(factory, database_list_create, _as(org, role), infra_id=infra.id)
    assert resp.status_code == 200


def test_database_row_with_disagreeing_org_is_hidden(factory, org, caplog):
    from api.models.database import Database
    from api.models.environment import Environment
    from api.views.database import database_detail, database_list_create

    infra = make_infra(organization=org)
    env = Environment.objects.get(infrastructure=infra)
    common = {"environment": env, "engine": "postgres", "engine_version": "16", "instance_class": "db.t4g.micro",
              "allocated_storage": 20, "status": "ACTIVE"}
    good = Database.objects.create(name="good", organization_id=org.id, **common)
    stray = Database.objects.create(name="stray", organization_id=make_org().id, **common)
    null_org = Database.objects.create(name="nullorg", organization_id=None, **common)
    user = _as(org, "owner")

    listed = call(factory, database_list_create, user, infra_id=infra.id)
    assert [d["id"] for d in listed.data] == [str(good.id)]
    with caplog.at_level(logging.ERROR):
        assert call(factory, database_detail, user, infra_id=infra.id, database_id=stray.id).status_code == 404
        assert call(factory, database_detail, user, infra_id=infra.id, database_id=null_org.id).status_code == 404
    assert "disagrees" in caplog.text


# ── CUSTOM_DOMAINS ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ROLES)
def test_custom_domain_write_matrix(factory, org, role):
    from api.views.custom_domain import custom_domain_detail, custom_domain_verify

    infra = make_infra(organization=org)
    for view in (custom_domain_detail, custom_domain_verify):
        method = "delete" if view is custom_domain_detail else "post"
        resp = call(factory, view, _as(org, role), method=method, infra_id=infra.id, domain_id=uuid.uuid4())
        assert resp.status_code == (404 if _allowed(role, Capability.CUSTOM_DOMAINS) else 403)


@pytest.mark.parametrize("role", ROLES)
def test_custom_domain_list_is_a_view_level_read(factory, org, role):
    from api.views.custom_domain import custom_domain_list_create

    infra = make_infra(organization=org)
    assert call(factory, custom_domain_list_create, _as(org, role), infra_id=infra.id).status_code == 200


# ── EXIT (reauth gate unchanged) ─────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ROLES)
def test_complete_exit_matrix(factory, org, role):
    from api.views.exit_export import infrastructure_complete_exit

    infra = make_infra(organization=org)
    resp = call(factory, infrastructure_complete_exit, _as(org, role, fresh_auth=True), method="post",
                data={"confirm": False}, infra_id=infra.id)
    if _allowed(role, Capability.EXIT):
        assert resp.status_code == 400
    else:
        assert resp.status_code == 403


@pytest.mark.parametrize("role", ROLES)
def test_exit_export_matrix(factory, org, role):
    from api.views.exit_export import exit_export

    infra = make_infra(organization=org)
    with patch("api.views.exit_export.exit_export_service.build", side_effect=RuntimeError("boom")):
        resp = call(factory, exit_export, _as(org, role, fresh_auth=True), infra_id=infra.id)
    if _allowed(role, Capability.EXIT):
        assert resp.status_code == 500
    else:
        assert resp.status_code == 403


@pytest.mark.parametrize("view_name,method", [("exit_export", "get"), ("infrastructure_complete_exit", "post")])
def test_stale_auth_time_is_refused_even_for_the_owner(factory, org, view_name, method):
    from api.views import exit_export as module

    infra = make_infra(organization=org)
    stale = token_user(org=org, account_id=add_member(org, "owner"))
    stale.payload["auth_time"] = int(time.time()) - 24 * 3600
    resp = call(factory, getattr(module, view_name), stale, method=method, data={"confirm": True} if method == "post" else None,
                infra_id=infra.id)
    assert resp.status_code == 401


# ── carry-forward owner-only sites ───────────────────────────────────────────────────

@pytest.mark.parametrize("role", ROLES)
def test_update_config_is_owner_only(factory, org, role):
    from api.views.infrastructure import infrastructure_update

    infra = make_infra(organization=org)
    resp = call(factory, infrastructure_update, _as(org, role), method="patch", data={"name": "renamed"}, infra_id=infra.id)
    assert resp.status_code == (200 if role == "owner" else 403)


@pytest.mark.parametrize("role", ROLES)
def test_reprovision_is_owner_only(factory, org, role):
    from api.views.infrastructure import infrastructure_reprovision

    infra = make_infra(organization=org, status="PROVISIONING")
    resp = call(factory, infrastructure_reprovision, _as(org, role), method="post", infra_id=infra.id)
    assert resp.status_code == (409 if role == "owner" else 403)


@pytest.mark.parametrize("role", ROLES)
def test_provisioning_logs_are_owner_only(factory, org, role):
    from api.views.provisioning_logs import provisioning_logs

    infra = make_infra(organization=org)
    resp = call(factory, provisioning_logs, _as(org, role), infra_id=infra.id)
    assert resp.status_code == (200 if role == "owner" else 403)


@pytest.mark.parametrize("role", ROLES)
def test_evidence_pack_is_owner_only(factory, org, role):
    from api.views.evidence_pack import evidence_pack

    infra = make_infra(organization=org)
    with patch("api.services.evidence_pack.build_evidence_pack", return_value=b"zip"):
        resp = call(factory, evidence_pack, _as(org, role), infra_id=infra.id)
    assert resp.status_code == (200 if role == "owner" else 403)


@pytest.mark.parametrize("role", ROLES)
def test_cfn_verify_and_refresh_are_owner_only(factory, org, role, settings):
    settings.CFN_ONBOARDING_ENABLED = True
    from api.views.cfn_onboarding import infrastructure_cfn_refresh as cfn_refresh
    from api.views.cfn_onboarding import infrastructure_cfn_verify as cfn_verify

    infra = make_infra(organization=org, is_mock=True)
    for view in (cfn_verify, cfn_refresh):
        resp = call(factory, view, _as(org, role), method="post", infra_id=infra.id)
        assert resp.status_code == (400 if role == "owner" else 403)


@pytest.mark.parametrize("role", ROLES)
def test_reissue_token_is_owner_only(factory, org, role):
    from api.views.infrastructure import infrastructure_reissue_token

    infra = make_infra(organization=org, is_cloud_authenticated=True)
    resp = call(factory, infrastructure_reissue_token, _as(org, role), method="post", infra_id=infra.id)
    assert resp.status_code == (409 if role == "owner" else 403)


# ── failure paths that apply to every converted site ─────────────────────────────────

SITES = [
    ("infrastructure", "infrastructure_detail", "get", None),
    ("costs", "infrastructure_costs", "get", None),
    ("nuke", "infrastructure_nuke", "post", {"confirm_name": "x"}),
    ("provisioning_logs", "provisioning_logs", "get", None),
    ("database", "database_list_create", "get", None),
    ("custom_domain", "custom_domain_list_create", "get", None),
    ("evidence_pack", "evidence_pack", "get", None),
]


def _call_site(factory, site, user, infra_id):
    import importlib

    module, name, method, data = site
    view = getattr(importlib.import_module(f"api.views.{module}"), name)
    return call(factory, view, user, method=method, data=data, infra_id=infra_id)


@pytest.mark.parametrize("site", SITES, ids=[s[1] for s in SITES])
def test_resource_in_another_org_is_404_even_for_an_owner_there(factory, org, site):
    other = make_org()
    infra = make_infra(organization=other)
    account = add_member(org, "owner")
    from api.models.membership import Membership
    Membership.objects.create(organization=other, account_id=account, role="owner", version=1)
    token_for_a = token_user(org=org, account_id=account)
    assert _call_site(factory, site, token_for_a, infra.id).status_code == 404


@pytest.mark.parametrize("site", SITES, ids=[s[1] for s in SITES])
def test_removed_member_is_404(factory, org, site):
    infra = make_infra(organization=org)
    gone = token_user(org=org, account_id=add_member(org, "owner", removed=True))
    assert _call_site(factory, site, gone, infra.id).status_code == 404


@pytest.mark.parametrize("site", SITES, ids=[s[1] for s in SITES])
def test_null_org_row_is_inaccessible_and_logged(factory, org, site, caplog):
    infra = make_infra(organization=None)
    owner = token_user(org=org, account_id=add_member(org, "owner"))
    with caplog.at_level(logging.WARNING):
        resp = _call_site(factory, site, owner, infra.id)
    assert resp.status_code == 404
    assert "no organization_id" in caplog.text


@pytest.mark.parametrize("site", SITES, ids=[s[1] for s in SITES])
def test_legacy_invited_user_grants_nothing(factory, org, site):
    """The invited_users M2M is how pre-F9 access worked. With the flag on, the legacy
    member has no Membership in the resource's org and so gets nothing."""
    infra = make_infra(organization=org)
    legacy_member = make_db_user()
    infra.invited_users.add(legacy_member)
    legacy_token = token_user(org=org, account_id=uuid.uuid4(), sub=legacy_member.id)
    assert _call_site(factory, site, legacy_token, infra.id).status_code == 404


def test_legacy_creator_who_is_not_a_member_loses_access(factory, org):
    from api.views.infrastructure import infrastructure_detail

    infra = make_infra(organization=org)
    creator_token = token_user(org=org, account_id=uuid.uuid4(), sub=infra.user_id)
    assert call(factory, infrastructure_detail, creator_token, infra_id=infra.id).status_code == 404


def test_token_without_account_id_resolves_no_role(factory, org):
    from api.views.infrastructure import infrastructure_detail

    infra = make_infra(organization=org)
    pre_f9 = token_user(org=org, account_id=None)
    assert call(factory, infrastructure_detail, pre_f9, infra_id=infra.id).status_code == 404


def test_malformed_infra_id_is_404_not_500(factory, org):
    from api.views.infrastructure import infrastructure_detail

    owner = token_user(org=org, account_id=add_member(org, "owner"))
    assert call(factory, infrastructure_detail, owner, infra_id="not-a-uuid").status_code == 404


def test_flag_on_without_actor_fails_closed(settings, fake_role_cache):
    from api.services.infrastructure import InfrastructureService

    settings.ORGS_ENABLED = True
    infra = make_infra(organization=make_org())
    assert InfrastructureService().get_infrastructure(infra.user_id, infra.id) is None


# ── mirror propagation ───────────────────────────────────────────────────────────────

def test_infra_created_event_carries_the_organization_id(org, no_budget, no_real_broker):
    from api.messaging.producer.producer import infra_producer

    infra_producer.publish_infra_created(user_id=uuid.uuid4(), infra_id=uuid.uuid4(), organization_id=org.id)
    import json
    payload = json.loads(no_real_broker[-1].body)["payload"]
    assert payload["organization_id"] == str(org.id)
