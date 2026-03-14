from api.models.infrastructure import Infrastructure
from api.models.user import User
from django.db.models import QuerySet, Q
from typing import Optional

from django.db import IntegrityError
from shared.errors.exception import HttpError
from shared.enums.user_role import UserRole

class InfrastructureRepository:
    def get_all_for_user(self, user_id) -> QuerySet:
        return Infrastructure.objects.filter(
            Q(user_id=user_id) | Q(invited_users__id=user_id)
        ).distinct()

    def get_by_id(self, user_id, infra_id) -> Optional[Infrastructure]:
        return Infrastructure.objects.filter(
            Q(user_id=user_id) | Q(invited_users__id=user_id),
            id=infra_id
        ).distinct().first()

    def create(self, user_id, infra_data) -> Infrastructure:
        try:
            user = User.objects.get(id=user_id)
            if user.role != UserRole.SUPER_ADMIN:
                raise HttpError(
                    message="Unauthorized",
                    status_code=403,
                    details="You are not authorized to create an infrastructure. Only Admins can perform this action."
                )
            infra = Infrastructure(user=user, **infra_data)
            infra.save()
            # NOTE: Event publishing is handled at the service layer (InfrastructureService.create_infrastructure)
            # to avoid duplicate events. Do NOT publish here.
            return infra
        except User.DoesNotExist:
            raise HttpError(
                message="User Synchronization Required",
                status_code=400,
                details=f"The user with ID {user_id} has not been synchronized from the auth service yet. Please ensure the user registration event has been processed."
            )
        except IntegrityError as e:
            raise e

    def update(self, user_id, infra_id, update_data) -> Optional[Infrastructure]:
        infra_qs = Infrastructure.objects.filter(user_id=user_id, id=infra_id)
        if infra_qs.exists():
            infra_qs.update(**update_data)
            return infra_qs.first()
        return None

    def delete(self, user_id, infra_id) -> bool:
        infra_qs = Infrastructure.objects.filter(user_id=user_id, id=infra_id)
        if infra_qs.exists():
            infra_qs.delete()
            return True
        return False
