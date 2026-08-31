import re
from uuid import uuid4

DNS_LABEL_RE = re.compile(r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?$')
MAX_K8S_SLUG_LENGTH = 59


def app_slug(name: str) -> str:
    """Sanitize an app name for use in AWS resource names / Docker tags."""
    return re.sub(r'[^a-z0-9._-]', '-', name.lower()).strip('-')


def image_tag(application) -> str:
    commit = (application.project_commit_hash or "").strip()
    if commit and commit.lower() not in ("none", "null"):
        return f"{app_slug(application.name)}-{commit[:12]}"
    return f"{app_slug(application.name)}-{uuid4().hex[:12]}"


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
