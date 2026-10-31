"""Turning ORGS_ENABLED back off after flag-on creates by an org admin is unsupported: the
startup guard refuses to boot, and the marker is only set for a non-owner creator. Also:
the legacy invite list is hidden under the flag, and a Database row takes its
infrastructure's org in the re-backfill."""
import importlib
from unittest.mock import patch

import pytest
from api.tests.org_authz_support import (
    add_member,
    call,
    make_db_user,
    make_infra,
    make_org,
    token_user,
)
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured

pytestmark = pytest.mark.django_db

CREATE_BODY = {"name": "new-infra", "cloud_provider": "aws", "code": "210987654321", "max_cpu": 4, "max_memory": 8}


@pytest.mark.parametrize("role,marked", [("owner", False), ("admin", True)])
def test_marker_is_set_only_when_a_non_owner_creates(factory, orgs_on, role, marked):
    from api.models.infrastructure import Infrastructure
    from api.views.infrastructure import infrastructure_list_create

    org = make_org()
    user = token_user(org=org, account_id=add_member(org, role), sub=make_db_user().id)
    resp = call(factory, infrastructure_list_create, user, method="post", data=CREATE_BODY)
    assert resp.status_code == 201
    assert Infrastructure.objects.get(id=resp.data["id"]).created_by_non_owner_under_orgs is marked


def test_flag_off_refuses_to_start_when_an_admin_created_under_the_flag(settings):
    from api.services.org_flag_guard import enforce_flag_off_safety

    make_infra(organization=make_org(), created_by_non_owner_under_orgs=True)
    settings.ORGS_ENABLED = False
    with pytest.raises(ImproperlyConfigured, match="unsupported"):
        enforce_flag_off_safety()


def test_flag_off_starts_when_only_owners_created(settings):
    from api.services.org_flag_guard import enforce_flag_off_safety

    make_infra(organization=make_org())
    settings.ORGS_ENABLED = False
    enforce_flag_off_safety()


def test_flag_on_never_blocks_startup(settings):
    from api.services.org_flag_guard import enforce_flag_off_safety

    make_infra(organization=make_org(), created_by_non_owner_under_orgs=True)
    settings.ORGS_ENABLED = True
    enforce_flag_off_safety()


def test_schema_commands_are_exempt(settings, monkeypatch):
    from api.services.org_flag_guard import enforce_flag_off_safety

    make_infra(organization=make_org(), created_by_non_owner_under_orgs=True)
    settings.ORGS_ENABLED = False
    monkeypatch.setattr("sys.argv", ["manage.py", "migrate"])
    enforce_flag_off_safety()


def test_invited_users_are_hidden_from_detail_and_list_under_the_flag(factory, orgs_on):
    from api.views.infrastructure import (
        infrastructure_detail,
        infrastructure_list_create,
    )

    org = make_org()
    infra = make_infra(organization=org)
    infra.invited_users.add(make_db_user())
    viewer = token_user(org=org, account_id=add_member(org, "viewer"))
    assert call(factory, infrastructure_detail, viewer, infra_id=infra.id).data["invited_users"] == []
    assert call(factory, infrastructure_list_create, viewer).data[0]["invited_users"] == []


def test_invited_users_still_shown_with_the_flag_off(factory, orgs_off):
    from api.views.infrastructure import infrastructure_detail
    from shared.utils.jwt import JWTUser

    infra = make_infra()
    member = make_db_user()
    infra.invited_users.add(member)
    data = call(factory, infrastructure_detail, JWTUser(sub=str(infra.user_id)), infra_id=infra.id).data
    assert [u["id"] for u in data["invited_users"]] == [str(member.id)]


def test_reprovision_lookup_race_is_404_not_500(factory, orgs_on):
    from api.views.infrastructure import infrastructure_reprovision

    org = make_org()
    infra = make_infra(organization=org)
    owner = token_user(org=org, account_id=add_member(org, "owner"))
    with patch("api.views.infrastructure.get_infra_with_capability", side_effect=LookupError):
        resp = call(factory, infrastructure_reprovision, owner, method="post", infra_id=infra.id)
    assert resp.status_code == 404


def test_rebackfill_gives_a_database_its_infrastructures_org():
    from api.models.database import Database
    from api.models.environment import Environment

    org = make_org()
    infra = make_infra(organization=org)
    db = Database.objects.create(
        environment=Environment.objects.get(infrastructure=infra), name="db", engine="postgres",
        engine_version="16", instance_class="db.t3.micro",
    )
    migration = importlib.import_module("api.migrations.0041_organization_id_rebackfill")
    migration.backfill_organization_id(apps, None)
    db.refresh_from_db()
    assert db.organization_id == org.id
