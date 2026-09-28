from django.db import models

from api.models.application import Application


class ApplicationRepository:
    """Repository for Application model operations."""
    def create(self, data: dict) -> Application:
        return Application.objects.create(**data)

    def get_by_id(self, application_id: str, defer_envs: bool = False) -> Application:
        qs = Application.objects.filter(id=application_id)
        if defer_envs:
            # H1 security review (REC2): a row whose envs doesn't decrypt under the
            # configured key must not take down a caller that never needed the value —
            # deferring means accessing .envs later still works (a lazy per-field reload),
            # it just isn't paid for, or risked, by callers that only need id/status/etc.
            qs = qs.defer('envs')
        return qs.first()

    def get_all_for_user(self, user_id: str, infra_id: str) -> models.QuerySet:
        # The list endpoint never serializes envs (api/views/application.py) — deferring
        # it here means a single row with an undecryptable envs value can't 500 the whole
        # list.
        owns_any = Application.objects.filter(user_id=user_id, infrastructure_id=infra_id).exists()
        if owns_any:
            return Application.objects.filter(user_id=user_id, infrastructure_id=infra_id).defer('envs')
        return Application.objects.filter(infrastructure_id=infra_id).defer('envs')

    def update(self, application_id: str, data: dict) -> Application:
        Application.objects.filter(id=application_id).update(**data)
        return self.get_by_id(application_id)

    def delete(self, application_id: str) -> bool:
        # This builds its own queryset rather than reusing what a caller may have already
        # fetched, and Collector.collect() evaluates *this* queryset (to check for
        # cascading relations) by forcing it — defer here too, or a corrupted envs value
        # blocks deletion of the very row meant to remove it.
        count, _ = Application.objects.filter(id=application_id).defer('envs').delete()
        return count > 0

    def get_total_resources_for_infra(self, infra_id: str) -> dict:
        return Application.objects.filter(infrastructure_id=infra_id).aggregate(
            total_cpu=models.Sum('alloted_cpu'),
            total_memory=models.Sum('alloted_memory'),
            total_storage=models.Sum('alloted_storage')
        )
