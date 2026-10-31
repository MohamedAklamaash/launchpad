"""Fixtures shared by the F9 ticket 7 authorization tests. No Redis, no broker, no AWS: the
role cache, the F0 budget, the deploy queue and the deploy lock are stubbed here."""
import uuid

from rest_framework.test import force_authenticate
from shared.enums.org_role import OrgRole

ROLES = [r.value for r in OrgRole]


def make_org(org_id=None):
    from api.models.organization import Organization

    return Organization.objects.create(id=org_id or uuid.uuid4(), owner_account_id=uuid.uuid4(), version=1)


def add_member(org, role, *, removed=False):
    from django.utils import timezone

    from api.models.membership import Membership

    account_id = uuid.uuid4()
    Membership.objects.create(
        organization=org, account_id=account_id, role=role, version=1,
        removed_at=timezone.now() if removed else None,
    )
    return account_id


def make_db_user():
    from api.models.user import User

    return User.objects.create(id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t")


def make_stack(*, organization, app_organization="same", owner=None, creator=None, env_status="PENDING", **app_extra):
    """An Infrastructure + Environment + Application. `app_organization` is "same" (the
    infra's org), None, or an Organization to disagree with."""
    from api.models.application import Application
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure

    owner = owner or make_db_user()
    infra = Infrastructure.objects.create(
        user=owner, name=f"infra-{uuid.uuid4()}", cloud_provider="aws", max_cpu=64, max_memory=128,
        code="123456789012", is_cloud_authenticated=True,
        organization_id=organization.id if organization else None,
    )
    Environment.objects.create(infrastructure=infra, status=env_status, vpc_id="vpc-1")
    if app_organization == "same":
        app_org_id = infra.organization_id
    else:
        app_org_id = app_organization.id if app_organization else None
    fields = {
        "user": creator or owner, "infrastructure": infra, "name": f"app-{uuid.uuid4().hex[:8]}",
        "project_remote_url": "https://github.com/o/r", "project_branch": "main", "project_commit_hash": "a" * 40,
        "envs": {"API_KEY": "placeholder-value"}, "port": 8080, "alloted_cpu": 0.25, "alloted_memory": 0.5,
        "attached_database_ids": [], "status": "ACTIVE", "organization_id": app_org_id, **app_extra,
    }
    app = Application.objects.create(**fields)
    return infra, app


def token_user(*, org, account_id, sub=None, fresh_auth=False):
    """request.user as the view sees it in this service: the DB User row that
    UserVerificationMiddleware swaps in, carrying the JWT's org_id and account_id."""
    from api.models.user import User

    user_id = sub or uuid.uuid4()
    user, _ = User.objects.get_or_create(
        id=user_id, defaults={"email": f"u-{uuid.uuid4()}@example.com", "user_name": "t"},
    )
    user.org_id = str(org.id) if org is not None else None
    user.account_id = str(account_id) if account_id else None
    return user


def call(factory, view, user, method="get", path="/x/", data=None, **view_kwargs):
    maker = getattr(factory, method)
    request = maker(path, data=data, format="json") if data is not None else maker(path)
    force_authenticate(request, user=user)
    return view(request, **view_kwargs)


def call_class_view(factory, view_class, user, method, data=None, **view_kwargs):
    maker = getattr(factory, method)
    request = maker("/x/", data=data, format="json") if data is not None else maker("/x/")
    force_authenticate(request, user=user)
    return view_class.as_view()(request, **view_kwargs)
