import logging
from django.db import transaction, IntegrityError
from api.models.user import User as UserModel
from shared.enums.user_role import UserRole

logger = logging.getLogger(__name__)


class PendingInvitedInfrastructure(Exception):
    """An invited infrastructure isn't materialized in the read-model yet. The user row is
    already committed; the caller should retry the link (bounded) rather than drop it."""

    def __init__(self, missing):
        self.missing = missing
        super().__init__(f"invited infrastructures not yet materialized: {missing}")


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

        defaults = {k: v for k, v in user_data.items() if k not in ("id", "infra_id", "roles")}

        try:
            # Commit the user independently of invite linking. auth.user.registered and
            # infrastructure.created race on separate queues; if an invited infra hasn't
            # materialized yet, the user must not be lost — the link is retried separately.
            with transaction.atomic():
                user, created = UserModel.objects.update_or_create(
                    id=user_id,
                    defaults=defaults,
                )
        except IntegrityError as exc:
            logger.error(
                "IntegrityError during user upsert",
                extra={"user_id": str(user_id), "error": str(exc)},
                exc_info=True,
            )
            raise

        action = "created" if created else "updated"
        logger.info(
            f"User {action} in local DB",
            extra={"user_id": str(user_id), "email": user_data.get("email")},
        )

        missing = self._link_invited_infrastructures(user, user_data)
        if missing:
            raise PendingInvitedInfrastructure(missing)
        return user, created

    @staticmethod
    def _link_invited_infrastructures(user, user_data: dict) -> list:
        infra_ids = user_data.get("infra_id", [])
        if not (infra_ids and user_data.get("invited_by")):
            return []
        from api.models.infrastructure import Infrastructure
        from api.models.infrastructure_user_role import InfrastructureUserRole
        roles = user_data.get("roles") or {}
        missing = []
        for iid in infra_ids:
            infra = Infrastructure.objects.filter(id=iid).first()
            if infra:
                infra.invited_users.add(user)
                # Per-infra role only — never the user's global role, which would grant their
                # highest role on every infra. A missing entry defaults to USER on create, but an
                # existing edge is only rewritten when the event carries an explicit role, so a
                # replayed/legacy event with a sparse roles map can't downgrade a live ADMIN.
                role = roles.get(str(iid))
                edge, created = InfrastructureUserRole.objects.get_or_create(
                    infrastructure=infra,
                    user=user,
                    defaults={"role": role or UserRole.USER},
                )
                if not created and role and edge.role != role:
                    edge.role = role
                    edge.save(update_fields=["role"])
            else:
                missing.append(str(iid))
        return missing