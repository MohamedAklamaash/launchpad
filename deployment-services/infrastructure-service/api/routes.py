from api.views.aws import list_aws_regions
from api.views.capabilities import list_capabilities
from api.views.database import database_detail, database_list_create
from api.views.health import health, liveness, readiness
from api.views.infrastructure import (
    infrastructure_detail,
    infrastructure_list_create,
    infrastructure_onboarding_callback,
    infrastructure_reissue_token,
    infrastructure_remove_user,
    infrastructure_reprovision,
    infrastructure_update,
)
from api.views.script_api_key import (
    infrastructure_policy_refresh_callback,
    script_api_key_issue,
)
from django.urls import path

urlpatterns = [
    path('infrastructures/', infrastructure_list_create, name='infrastructure-list-create'),
    # Must precede the <str:infra_id> catch-all: 'script-api-key' is a single path
    # segment and would otherwise be swallowed as an infra id.
    path('infrastructures/script-api-key/', script_api_key_issue, name='script-api-key-issue'),
    path('infrastructures/policy-refresh/callback/', infrastructure_policy_refresh_callback, name='infrastructure-policy-refresh-callback'),
    path('infrastructures/<str:infra_id>/databases/', database_list_create, name='database-list-create'),
    path('infrastructures/<str:infra_id>/databases/<str:database_id>/', database_detail, name='database-detail'),
    path('infrastructures/<str:infra_id>/', infrastructure_detail, name='infrastructure-detail'),
    path('infrastructures/<str:infra_id>/update/', infrastructure_update, name='infrastructure-update'),
    path('infrastructures/<str:infra_id>/reprovision/', infrastructure_reprovision, name='infrastructure-reprovision'),
    path('infrastructures/<str:infra_id>/reissue-token/', infrastructure_reissue_token, name='infrastructure-reissue-token'),
    path('infrastructures/onboarding/callback/', infrastructure_onboarding_callback, name='infrastructure-onboarding-callback'),
    path('infrastructures/<str:infra_id>/users/<str:user_id>/', infrastructure_remove_user, name='infrastructure-remove-user'),
    path('healthz/', health, name='health'),
    path('liveness/', liveness, name='liveness'),
    path('readiness/', readiness, name='readiness'),
    path('aws/regions/', list_aws_regions, name='aws-regions'),
    path('capabilities/', list_capabilities, name='capabilities'),
]
