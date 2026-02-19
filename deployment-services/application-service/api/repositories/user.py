import logging
from django.db import transaction, IntegrityError
from api.models.user import User as UserModel

logger = logging.getLogger(__name__)


class UserRepository:
    def get_user(self, user_id):
        try:
            return UserModel.objects.get(id=user_id)
        except UserModel.DoesNotExist:
            return None

    def upsert_user(self, user_data: dict):
        """
        Idempotent upsert for User rows received from RabbitMQ auth events.
        """
        user_id = user_data.get("id")
        if not user_id:
            raise ValueError("User ID is required for upsert operation")

        defaults = {k: v for k, v in user_data.items() if k != "id"}

        try:
            with transaction.atomic():
                user, created = UserModel.objects.update_or_create(
                    id=user_id,
                    defaults=defaults,
                )
            action = "created" if created else "updated"
            logger.info(
                f"User {action} in local DB",
                extra={"user_id": str(user_id), "email": user_data.get("email")},
            )
            return user, created
        except IntegrityError as exc:
            logger.error(
                "IntegrityError during user upsert",
                extra={"user_id": str(user_id), "error": str(exc)},
                exc_info=True,
            )
            raise