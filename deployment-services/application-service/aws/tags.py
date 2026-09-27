"""Shared tag construction for per-app AWS resources.

Every resource a deploy creates for a given app carries `launchpad:infra` and
`launchpad:app` so Cost Explorer can group spend by app once cost allocation tags are
activated (see infrastructure-service's `cost_service.py`). Tags are not retroactive —
a resource created before this module existed stays untagged until either redeployed or
picked up by `tag_existing_app_resources` (application-service management command).
"""

TAG_INFRA_KEY = "launchpad:infra"
TAG_APP_KEY = "launchpad:app"


def app_tags(infra_id, app_name: str) -> dict[str, str]:
    """Tags for a resource that belongs to exactly one app."""
    return {TAG_INFRA_KEY: str(infra_id), TAG_APP_KEY: app_name}


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
