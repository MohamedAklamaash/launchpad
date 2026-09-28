import hashlib
import re
from uuid import uuid4

DNS_LABEL_RE = re.compile(r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?$')
MAX_K8S_SLUG_LENGTH = 59
MAX_TARGET_GROUP_NAME_LENGTH = 32


def app_slug(name: str) -> str:
    """Sanitize an app name for use in AWS resource names / Docker tags."""
    return re.sub(r'[^a-z0-9._-]', '-', name.lower()).strip('-')


def image_tag(application) -> str:
    commit = (application.project_commit_hash or "").strip()
    if commit and commit.lower() not in ("none", "null"):
        return f"{app_slug(application.name)}-{commit[:12]}"
    return f"{app_slug(application.name)}-{uuid4().hex[:12]}"


def _resource_discriminator(*parts) -> str:
    """A full-id hash, never a UUID prefix (CLAUDE.md): the leading 48 bits of a
    UUIDv7 are a Unix-millisecond timestamp, so a short *prefix* of the id repeats
    every ~65s platform-wide and is forceable from a row's own `created_at`. Hashing
    the full value first and truncating only the *hash output* — the same pattern as
    `Database.module_name()` — has no such structure to force. Every part is hashed
    together so the caller can bind a name to more than one id (e.g. both the
    infrastructure and the application) in one discriminator."""
    seed = ":".join(str(p) for p in parts)
    return hashlib.sha256(seed.encode()).hexdigest()[:8]


def target_group_name(application) -> str:
    """ALB target group name (H4). Hard 32-char ALB limit.

    Discriminated by both the infrastructure id AND the application id, not the
    infrastructure alone: two infras with the same app slug is the collision this
    exists to prevent, but a deleted-then-recreated *application* on the very same
    infra also needs a distinct name — its predecessor's target group can be left
    behind (application_cleanup_service._delete_target_group gives up, but does not
    stop retrying forever, after 6 attempts against ResourceInUseException) still
    carrying that predecessor's own launchpad:app/launchpad:infra tags, which an
    infra-only discriminator would reproduce byte-for-byte and the tag-ownership check
    in ALBClient.create_target_group would then wrongly accept as "ours".

    The app-name slug is truncated to make room, never the hash suffix — truncating
    the whole `f"{slug}-{hash}-tg"` string (the previous bug) could cut into or
    entirely remove the discriminator for a long app name. Also re-restricted to
    `[a-z0-9-]`: `app_slug` still admits '.' and '_' for Docker tags, but ALB target
    group names reject both.
    """
    slug = re.sub(r'[^a-z0-9-]', '-', app_slug(application.name)).strip('-') or "app"
    digest = _resource_discriminator(application.infrastructure_id, application.id)
    suffix = f"-{digest}-tg"
    max_slug_len = MAX_TARGET_GROUP_NAME_LENGTH - len(suffix)
    return f"{slug[:max_slug_len]}{suffix}"


def ecs_log_group(slug: str) -> str:
    """Legacy CloudWatch log group name, shared by every app with this slug on every
    infrastructure in the same AWS account+region (H4) — kept only as the fallback for
    an `Application` row that already deployed under this name before
    `Application.log_group_name` existed. A row's own stored name, once persisted,
    never changes back to this. See `new_ecs_log_group` for what a genuinely new
    deploy uses, and `ecs_log_group_for` for the single place that decides between the
    two when reading."""
    return f"/ecs/{slug}-task"


def new_ecs_log_group(application) -> str:
    """CloudWatch log group name for a genuinely new deploy (H4) — discriminated by a
    hash of both the infrastructure id and the application id, for the same
    delete-then-recreate reason as `target_group_name`. Computed once, the first time
    an `Application` row is deployed, and persisted to `Application.log_group_name`
    (see application_deployment_service._create_task_definition); every later
    deploy/rollback/backfill of that row, plus runtime-log tailing, cleanup, and the
    exit-export inventory, read the stored value back through `ecs_log_group_for`
    rather than recomputing it."""
    slug = app_slug(application.name)
    digest = _resource_discriminator(application.infrastructure_id, application.id)
    return f"/ecs/{slug}-{digest}-task"


def ecs_log_group_for(application) -> str:
    """The log group this application's containers actually log to right now: the
    stored name (H4) if one was ever persisted, else the legacy shared-per-slug name
    for a row that deployed before `Application.log_group_name` existed. Every reader
    — runtime-log tailing, cleanup, the exit-export inventory — calls this instead of
    re-deriving either name itself."""
    return application.log_group_name or ecs_log_group(app_slug(application.name))


def ecs_task_family(slug: str) -> str:
    """Legacy ECS task-definition family, shared by every app with this slug on every
    infrastructure in the same AWS account+region (H7 residual from H4) — kept only as
    the fallback for an `Application` row that already deployed under this name before
    `Application.task_family` existed. A row's own stored family, once persisted, never
    changes back to this. See `new_ecs_task_family` for what a genuinely new deploy
    uses, and `ecs_task_family_for` for the single place that decides between the two
    when reading."""
    return f"{slug}-task"


def new_ecs_task_family(application) -> str:
    """ECS task-definition family for a genuinely new deploy (H7 residual from H4) —
    discriminated by a hash of both the infrastructure id and the application id, the
    same pattern as `new_ecs_log_group`. Computed once, the first time an `Application`
    row is deployed, and persisted to `Application.task_family`; every later
    deploy/rollback/backfill of that row, plus runtime-log tailing and the exit-export
    inventory, read the stored value back through `ecs_task_family_for` rather than
    recomputing it. The family also names the app container inside the task
    definition, so `ECSClient.create_service`'s `container_name` must be given this
    same value."""
    slug = app_slug(application.name)
    digest = _resource_discriminator(application.infrastructure_id, application.id)
    return f"{slug}-{digest}-task"


def ecs_task_family_for(application) -> str:
    """The ECS task-definition family this application's containers actually run
    under right now: the stored family (H7) if one was ever persisted, else the
    legacy shared-per-slug name for a row that deployed before
    `Application.task_family` existed. Every reader — runtime-log tailing, the
    exit-export inventory — calls this instead of re-deriving either name itself."""
    return application.task_family or ecs_task_family(app_slug(application.name))


def require_k8s_safe_slug(name: str) -> str:
    """Slugs admit '.' and '_' (legal in Docker tags) which k8s object names reject.
    Refuse rather than re-sanitize: 'my.app' and 'my-app' would collapse onto one
    namespace and silently overwrite each other."""
    slug = app_slug(name)
    if not DNS_LABEL_RE.match(slug) or len(slug) > MAX_K8S_SLUG_LENGTH:
        raise ValueError(
            f"Application name '{name}' is not deployable on Kubernetes: it must be at most "
            f"{MAX_K8S_SLUG_LENGTH} lowercase alphanumeric characters or hyphens"
        )
    return slug
