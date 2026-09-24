import logging
import uuid

from api.models.environment import Environment
from api.repositories.infrastructure import InfrastructureRepository
from api.services.log_redaction import redact_provisioning_text
from api.services.terraform_worker import MAX_LOG_CHARS

logger = logging.getLogger(__name__)

# The storage cap clips on a line boundary, so a clipped value lands under MAX_LOG_CHARS
# by up to one line rather than exactly on it. Comparing for equality would report every
# clipped log as untruncated. The slack is far wider than any real terraform line; the
# only cost of over-reporting is a badge on a log that happened to stop just under the cap.
_CLIP_SLACK = 4096



class ProvisioningLogsService:
    def __init__(self):
        self.infra_repo = InfrastructureRepository()

    def get_logs(self, user_id, infra_id):
        env = self._get_owned_environment(user_id, infra_id)
        truncated = len(env.logs or "") >= MAX_LOG_CHARS - _CLIP_SLACK
        logs = _reread(infra_id, "logs", env.logs)
        error = _reread(infra_id, "error_message", env.error_message)
        return {
            "status": env.status,
            "error_message": error.text or None,
            "logs": logs.text,
            "withheld_lines": logs.withheld_lines,
            "truncated": truncated,
            "updated_at": env.updated_at,
        }

    def _get_owned_environment(self, user_id, infra_id):
        # A malformed id reaching filter(id=...) on a UUIDField raises
        # django.core.exceptions.ValidationError — not a ValueError — and would escape as a 500.
        try:
            uuid.UUID(str(infra_id))
        except ValueError:
            raise LookupError("Infrastructure not found")
        infra = self.infra_repo.get_by_id(user_id, infra_id)
        if not infra:
            raise LookupError("Infrastructure not found")
        if str(infra.user_id) != str(user_id):
            raise PermissionError("Only the infrastructure owner can view provisioning logs")
        try:
            return Environment.objects.get(infrastructure_id=infra.id)
        except Environment.DoesNotExist:
            raise LookupError("Environment not found")


def _reread(infra_id, field, stored):
    """Every write site redacts and truncates on a line boundary, so a stored value is a
    redactor fixed point. Any difference means a write path bypassed the redactor; the
    re-redacted value is served either way, and the warning carries no row content."""
    result = redact_provisioning_text(stored)
    if result.text != (stored or ""):
        logger.warning(f"Stored {field} for infra {infra_id} was not redacted at write time; serving re-redacted value")
    return result
