"""F9 ticket 7, flag OFF: every converted site behaves exactly as it did before this
ticket. The legacy owner / invited_users model decides; Membership rows, org claims and
account ids are ignored. Statuses here are the pre-F9 ones."""
import uuid
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
from shared.utils.jwt import JWTUser
from shared.utils.org_id import personal_org_id

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_infra_queue(monkeypatch):
    from unittest.mock import MagicMock

    fake = MagicMock()
    monkeypatch.setattr("api.services.nuke_service.InfraQueue", fake)
    monkeypatch.setattr("api.services.infrastructure.InfraQueue", fake)
    monkeypatch.setattr("api.services.database_service.InfraQueue", fake)


def legacy_token(user, **extra):
    return JWTUser(sub=str(user.id), **extra)


@pytest.fixture
def world(orgs_off):
    owner = make_db_user()
    invited = make_db_user()
    stranger = make_db_user()
    infra = make_infra(owner=owner, name="prod")
    infra.invited_users.add(invited)
    return owner, invited, stranger, infra


def _views():
    from api.views import (
        costs,
        custom_domain,
        database,
        evidence_pack,
        infrastructure,
        nuke,
        provisioning_logs,
    )

    return {
        "detail": (infrastructure.infrastructure_detail, "get", None),
        "update": (infrastructure.infrastructure_update, "patch", {"name": "renamed"}),
        "delete": (infrastructure.infrastructure_detail, "delete", None),
        "reprovision": (infrastructure.infrastructure_reprovision, "post", None),
        "reissue": (infrastructure.infrastructure_reissue_token, "post", None),
        "nuke_start": (nuke.infrastructure_nuke, "post", {"confirm_name": "nope"}),
        "costs": (costs.infrastructure_costs, "get", None),
        "prov_logs": (provisioning_logs.provisioning_logs, "get", None),
        "evidence": (evidence_pack.evidence_pack, "get", None),
        "db_create": (database.database_list_create, "post", {"name": "d"}),
        "db_list": (database.database_list_create, "get", None),
        "domain_list": (custom_domain.custom_domain_list_create, "get", None),
        "domain_verify": (custom_domain.custom_domain_verify, "post", None),
    }


# (view, role-of-caller) -> pre-F9 status. owner row: the status an owner got.
EXPECTED = {
    "detail": {"owner": 200, "invited": 200, "stranger": 404},
    "update": {"owner": 200, "invited": 403, "stranger": 404},
    "delete": {"owner": 204, "invited": 403, "stranger": 404},
    "reprovision": {"owner": 202, "invited": 403, "stranger": 404},
    "reissue": {"owner": 409, "invited": 403, "stranger": 404},
    "nuke_start": {"owner": 409, "invited": 403, "stranger": 404},
    "costs": {"owner": 200, "invited": 403, "stranger": 404},
    "prov_logs": {"owner": 200, "invited": 403, "stranger": 404},
    "evidence": {"owner": 200, "invited": 403, "stranger": 404},
    "db_create": {"owner": 400, "invited": 403, "stranger": 404},
    "db_list": {"owner": 200, "invited": 200, "stranger": 404},
    "domain_list": {"owner": 200, "invited": 200, "stranger": 404},
    "domain_verify": {"owner": 404, "invited": 403, "stranger": 404},
}


@pytest.mark.parametrize("who", ["owner", "invited", "stranger"])
@pytest.mark.parametrize("name", list(EXPECTED))
def test_legacy_status_for_every_converted_site(factory, world, name, who):
    owner, invited, stranger, infra = world
    caller = {"owner": owner, "invited": invited, "stranger": stranger}[who]
    view, method, data = _views()[name]
    kwargs = {"infra_id": infra.id}
    if name == "domain_verify":
        kwargs["domain_id"] = uuid.uuid4()
    with patch("api.services.cost_service.get_infrastructure_costs", return_value={"ok": True}), \
         patch("api.services.evidence_pack.build_evidence_pack", return_value=b"zip"):
        resp = call(factory, view, legacy_token(caller), method=method, data=data, **kwargs)
    assert resp.status_code == EXPECTED[name][who]


def test_owner_delete_of_pending_infra_still_deletes(factory, orgs_off):
    from api.models.infrastructure import Infrastructure
    from api.views.infrastructure import infrastructure_detail

    owner = make_db_user()
    infra = make_infra(owner=owner, status="PENDING")
    resp = call(factory, infrastructure_detail, legacy_token(owner), method="delete", infra_id=infra.id)
    assert resp.status_code == 204
    assert not Infrastructure.objects.filter(id=infra.id).exists()


def test_list_returns_owned_and_invited_not_stranger(factory, world):
    from api.views.infrastructure import infrastructure_list_create

    _, invited, stranger, infra = world
    other = make_infra(owner=stranger)
    assert [i["id"] for i in call(factory, infrastructure_list_create, legacy_token(invited)).data] == [str(infra.id)]
    assert [i["id"] for i in call(factory, infrastructure_list_create, legacy_token(stranger)).data] == [str(other.id)]


def test_membership_rows_and_org_claims_are_ignored(factory, world):
    from api.views.infrastructure import infrastructure_detail

    _, _, stranger, infra = world
    org = make_org()
    account = add_member(org, "owner")
    claimed = token_user(org=org, account_id=account, sub=stranger.id)
    assert call(factory, infrastructure_detail, claimed, infra_id=infra.id).status_code == 404


def test_pre_f9_token_with_no_org_claims_still_works(factory, world):
    from api.views.infrastructure import infrastructure_detail

    owner, _, _, infra = world
    assert call(factory, infrastructure_detail, legacy_token(owner), infra_id=infra.id).status_code == 200


CREATE_BODY = {"name": "new-infra", "cloud_provider": "aws", "code": "210987654321", "max_cpu": 4, "max_memory": 8}


def test_create_still_requires_the_global_super_admin_role(factory, orgs_off):
    from api.views.infrastructure import infrastructure_list_create

    plain = make_db_user(role="user")
    resp = call(factory, infrastructure_list_create, legacy_token(plain), method="post", data=CREATE_BODY)
    assert resp.status_code == 403
    assert resp.data == {"error": "Unauthorized"}


def test_create_stamps_the_creators_personal_org_and_ignores_client_org(factory, orgs_off):
    from api.models.infrastructure import Infrastructure
    from api.views.infrastructure import infrastructure_list_create

    owner = make_db_user()
    body = {**CREATE_BODY, "organization_id": str(uuid.uuid4())}
    resp = call(factory, infrastructure_list_create, legacy_token(owner), method="post", data=body)
    assert resp.status_code == 201
    assert Infrastructure.objects.get(id=resp.data["id"]).organization_id == personal_org_id(owner.id)


def test_null_org_row_is_still_reachable_by_its_legacy_owner(factory, orgs_off):
    from api.views.infrastructure import infrastructure_detail

    owner = make_db_user()
    infra = make_infra(owner=owner, organization=None)
    assert call(factory, infrastructure_detail, legacy_token(owner), infra_id=infra.id).status_code == 200
