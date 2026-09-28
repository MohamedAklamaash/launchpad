"""Shared tag construction for per-app AWS resources.

Every resource a deploy creates for a given app carries `launchpad:infra` and
`launchpad:app` so Cost Explorer can group spend by app once cost allocation tags are
activated (see infrastructure-service's `cost_service.py`). Tags are not retroactive —
a resource created before this module existed stays untagged until either redeployed or
picked up by `tag_existing_app_resources` (application-service management command).
"""
from shared.aws.cost_tags import TAG_APP_KEY, TAG_INFRA_KEY

# H4 C1: not part of app_tags()/Cost Explorer's grouping keys — this identifies a
# specific Application ROW, not its (mutable, reusable) name. Only target groups carry
# it today, as the extra input to ALBClient's ownership check (see
# target_group_tags/_verify_target_group_ownership): app_tags() alone is
# launchpad:infra + launchpad:app, both name-shaped, so a delete-then-recreate of an
# app under the same name on the same infra would tag its new target group identically
# to its predecessor's on that axis alone. target_group_name()'s per-row hash already
# makes the two land on different names in practice; this tag makes the ownership
# *check* not have to rely on that alone.
TAG_APP_ID_KEY = "launchpad:app-id"


def app_tags(infra_id, app_name: str) -> dict[str, str]:
    """Tags for a resource that belongs to exactly one app."""
    return {TAG_INFRA_KEY: str(infra_id), TAG_APP_KEY: app_name}


def target_group_tags(infra_id, app_name: str, app_id) -> dict[str, str]:
    """`app_tags()` plus the owning Application row's own id — see TAG_APP_ID_KEY."""
    return {**app_tags(infra_id, app_name), TAG_APP_ID_KEY: str(app_id)}


def infra_tags(infra_id) -> dict[str, str]:
    """`launchpad:infra` only, for a resource shared by every app in the infrastructure.

    The CodeBuild project and its service role are named per-infrastructure
    (`launchpad-build-{infra_id}` / `launchpad-codebuild-role-{infra_id}`) and build
    every app on that infra — tagging either with one app's name would attribute every
    other app's build minutes to that app. Cost Explorer reports this spend on the
    infra-level `shared` line instead.
    """
    return {TAG_INFRA_KEY: str(infra_id)}


def as_key_value_tags(tags: dict[str, str]) -> list[dict]:
    """`Key`/`Value` shape used by ELBv2 (ALB) and IAM."""
    return [{"Key": k, "Value": v} for k, v in tags.items()]


def as_lower_tags(tags: dict[str, str]) -> list[dict]:
    """`key`/`value` shape used by ECS and CodeBuild."""
    return [{"key": k, "value": v} for k, v in tags.items()]
