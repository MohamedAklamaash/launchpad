from django.urls import path
from api.views.infrastructure import infrastructure_list_create, infrastructure_detail, infrastructure_update
from api.views.health import health, liveness, readiness

urlpatterns = [
    path('infrastructures/', infrastructure_list_create, name='infrastructure-list-create'),
    path('infrastructures/<uuid:infra_id>/', infrastructure_detail, name='infrastructure-detail'),
    path('infrastructures/<uuid:infra_id>/update/', infrastructure_update, name='infrastructure-update'),
    path('healthz/', health, name='health'),
    path('liveness/', liveness, name='liveness'),
    path('readiness/', readiness, name='readiness'),
]
