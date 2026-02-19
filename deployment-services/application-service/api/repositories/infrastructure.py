import logging
from django.db import transaction, IntegrityError
from django.db.models import Q

from api.models.infrastructure import Infrastructure
from api.models.user import User


logger = logging.getLogger(__name__)


class InfrastructureRepository:
    def get_infrastructure(self, infra_id):
        try:
            return Infrastructure.objects.get(id=infra_id)
        except Infrastructure.DoesNotExist:
            return None

    def is_user_authorized(self, infra_id: str, user_id: str) -> bool:
        """Check if user owns or is invited to the infrastructure."""
        return Infrastructure.objects.filter(
            Q(id=infra_id) &
            (Q(user_id=user_id) | Q(invited_users__id=user_id))
        ).exists()

    def upsert_infrastructure(self, infra_data: dict):
        """
        Idempotent upsert for Infrastructure rows received from RabbitMQ events.
        """
        infra_id = infra_data.get("id")
        if not infra_id:
            raise ValueError("Infrastructure ID is required for upsert")

        user_id = infra_data.get("user_id")
        if not user_id:
            raise ValueError("user_id is required for upsert")

        # Resolve the FK — if the user hasn't been synced yet, skip and NACK
        # so the consumer will retry (caller handles the exception).
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            logger.error(
                "upsert_infrastructure: user not found in local DB — user sync may be lagging",
                extra={"infra_id": str(infra_id), "user_id": str(user_id)},
            )
            raise

        defaults = {
            "user": user,
            "name": infra_data.get("name") or "",
            "cloud_provider": infra_data.get("cloud_provider") or "",
            "max_cpu": float(infra_data.get("max_cpu") or 0),
            "max_memory": float(infra_data.get("max_memory") or 0),
        }
        if "metadata" in infra_data:
            defaults["metadata"] = infra_data["metadata"]

        try:
            with transaction.atomic():
                infra, created = Infrastructure.objects.update_or_create(
                    id=infra_id,
                    defaults=defaults,
                )
            action = "created" if created else "updated"
            logger.info(
                f"Infrastructure {action} in local DB",
                extra={"infra_id": str(infra_id), "user_id": str(user_id)},
            )
            return infra, created
        except IntegrityError as exc:
            logger.error(
                "IntegrityError during infrastructure upsert",
                extra={"infra_id": str(infra_id), "user_id": str(user_id), "error": str(exc)},
                exc_info=True,
            )
            raise
