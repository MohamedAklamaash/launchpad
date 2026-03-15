from django.urls import path
from api.views.infrastructure import infrastructure_list_create, infrastructure_detail, infrastructure_update, infrastructure_remove_user, infrastructure_reprovision
from api.views.health import health, liveness, readiness

urlpatterns = [
    path('infrastructures/', infrastructure_list_create, name='infrastructure-list-create'),
    path('infrastructures/<str:infra_id>/', infrastructure_detail, name='infrastructure-detail'),
    path('infrastructures/<str:infra_id>/update/', infrastructure_update, name='infrastructure-update'),
    path('infrastructures/<str:infra_id>/reprovision/', infrastructure_reprovision, name='infrastructure-reprovision'),
    path('infrastructures/<str:infra_id>/users/<str:user_id>/', infrastructure_remove_user, name='infrastructure-remove-user'),
    path('healthz/', health, name='health'),
    path('liveness/', liveness, name='liveness'),
    path('readiness/', readiness, name='readiness'),
]
