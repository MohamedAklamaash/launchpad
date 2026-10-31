"""Fixtures shared by the F9 ticket 7 authorization tests. No Redis, no broker: the role
cache and the F0 budget are stubbed here."""
import time
import uuid

from rest_framework.test import force_authenticate
from shared.enums.org_role import OrgRole
from shared.utils.jwt import JWTUser

ROLES = [r.value for r in OrgRole]


def make_org(org_id=None):
    from api.models.organization import Organization

    return Organization.objects.create(id=org_id or uuid.uuid4(), owner_account_id=uuid.uuid4(), version=1)


def add_member(org, role, *, removed=False):
    """A Membership row for a fresh account; returns that account id."""
    from api.models.membership import Membership
    from django.utils import timezone

    account_id = uuid.uuid4()
    Membership.objects.create(
        organization=org, account_id=account_id, role=role, version=1,
        removed_at=timezone.now() if removed else None,
    )
    return account_id


def make_db_user(*, role="super_admin"):
    from api.models.user import User

    return User.objects.create(
        id=uuid.uuid4(), email=f"u-{uuid.uuid4()}@example.com", user_name="t", role=role,
    )


def make_infra(*, owner=None, organization=None, name=None, status="ACTIVE", **extra):
    from api.models.environment import Environment
    from api.models.infrastructure import Infrastructure

    owner = owner or make_db_user()
    fields = {
        "user": owner, "name": name or f"infra-{uuid.uuid4()}", "cloud_provider": "aws",
        "max_cpu": 1024, "max_memory": 512, "code": "123456789012", "metadata": {"aws_region": "us-east-1"},
        "is_cloud_authenticated": True, "organization_id": organization.id if organization else None,
        "dns_label": uuid.uuid4().hex[:16], **extra,
    }
    infra = Infrastructure.objects.create(**fields)
    Environment.objects.create(infrastructure=infra, status=status, vpc_id="vpc-abc123")
    return infra


def token_user(*, org, account_id, sub=None, fresh_auth=False):
    """What JWTAuthMiddleware leaves on request.user with ORGS_ENABLED on."""
    claims = {
        "sub": str(sub or uuid.uuid4()),
        "org_id": str(org.id) if org is not None else None,
        "account_id": str(account_id) if account_id else None,
    }
    if fresh_auth:
        claims["auth_time"] = int(time.time())
    return JWTUser(**claims)


def call(factory, view, user, method="get", path="/x/", data=None, **view_kwargs):
    request = getattr(factory, method)(path, data=data, format="json") if data is not None else getattr(factory, method)(path)
    force_authenticate(request, user=user)
    return view(request, **view_kwargs)
