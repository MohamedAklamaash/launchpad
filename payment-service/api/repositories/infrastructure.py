import logging
import uuid

from api.models.infrastructure import Infrastructure

logger = logging.getLogger(__name__)


class InfrastructureRepository:
    def get_infrastructure(self, infra_id):
        try:
            return Infrastructure.objects.get(id=infra_id)
        except Infrastructure.DoesNotExist:
            return None

    def upsert_infrastructure(self, infra_data):
        infra_id = infra_data.get('id')
        if not infra_id:
            raise ValueError("Infrastructure ID is required for upsert")
        
        infra_data = dict(infra_data)
        incoming_org = self._canonical_organization_id(infra_data.pop("organization_id", None))
        existing = Infrastructure.objects.filter(id=infra_id).first()
        if incoming_org:
            if existing is None or existing.organization_id is None:
                infra_data["organization_id"] = incoming_org
            elif str(existing.organization_id) != incoming_org:
                logger.error(
                    "upsert_infrastructure: refusing to overwrite an already-set organization_id "
                    "with a different value",
                    extra={"infra_id": str(infra_id)},
                )
        infra, created = Infrastructure.objects.update_or_create(
            id=infra_id,
            defaults=infra_data
        )
        return infra, created

    @staticmethod
    def _canonical_organization_id(value):
        if not value:
            return None
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, TypeError):
            logger.error("upsert_infrastructure: rejecting malformed organization_id in event payload")
            return None
