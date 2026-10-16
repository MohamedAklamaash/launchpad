from django.db import models


class OrgRole(models.TextChoices):
    """Mirrors identity-services/services/auth-service/src/types/org-role.ts's ORG_ROLE —
    the four-role matrix from plan/F9-teams-and-orgs.md (Decision: Role set). Shared so
    infrastructure-service, application-service, and payment-service all validate
    org.events payloads against the exact same set."""

    OWNER = "owner"
    ADMIN = "admin"
    DEVELOPER = "developer"
    VIEWER = "viewer"
