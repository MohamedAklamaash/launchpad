from api.views.aws import list_aws_regions
from api.views.capabilities import list_capabilities
from api.views.costs import infrastructure_costs
from api.views.custom_domain import (
    custom_domain_detail,
    custom_domain_list_create,
    custom_domain_verify,
)
from api.views.custom_domain_internal import custom_domain_disable_for_application
from api.views.database import database_detail, database_list_create
from api.views.evidence_pack import evidence_pack
from api.views.exit_export import exit_export, infrastructure_complete_exit
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
from api.views.infrastructure_internal import infrastructure_exit_status
from api.views.nuke import infrastructure_nuke
from api.views.provisioning_logs import provisioning_logs
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
    path('infrastructures/<str:infra_id>/costs/', infrastructure_costs, name='infrastructure-costs'),
    path('infrastructures/<str:infra_id>/custom-domains/', custom_domain_list_create, name='custom-domain-list-create'),
    path('infrastructures/<str:infra_id>/custom-domains/<str:domain_id>/', custom_domain_detail, name='custom-domain-detail'),
    path('infrastructures/<str:infra_id>/custom-domains/<str:domain_id>/verify/', custom_domain_verify, name='custom-domain-verify'),
    # Internal-only, fixed literal path (no gateway route). Called same-origin by
    # application-service's app-delete cleanup (F1b part 3b).
    path('internal/custom-domains/disable-for-application/', custom_domain_disable_for_application, name='custom-domain-disable-for-application'),
    # H2 RECOMMENDED 2: fixed literal path, infrastructure_id in the query string — see
    # api/views/infrastructure_internal.py's docstring for why (no gateway route either).
    path('internal/infrastructures/exit-status/', infrastructure_exit_status, name='infrastructure-exit-status'),
    path('infrastructures/<str:infra_id>/', infrastructure_detail, name='infrastructure-detail'),
    path('infrastructures/<str:infra_id>/update/', infrastructure_update, name='infrastructure-update'),
    path('infrastructures/<str:infra_id>/reprovision/', infrastructure_reprovision, name='infrastructure-reprovision'),
    path('infrastructures/<str:infra_id>/reissue-token/', infrastructure_reissue_token, name='infrastructure-reissue-token'),
    path('infrastructures/<str:infra_id>/logs/', provisioning_logs, name='provisioning-logs'),
    path('infrastructures/<str:infra_id>/evidence-pack/', evidence_pack, name='evidence-pack'),
    path('infrastructures/<str:infra_id>/exit-export/', exit_export, name='exit-export'),
    path('infrastructures/<str:infra_id>/exit/', infrastructure_complete_exit, name='infrastructure-complete-exit'),
    path('infrastructures/<str:infra_id>/nuke/', infrastructure_nuke, name='infrastructure-nuke'),
    path('infrastructures/onboarding/callback/', infrastructure_onboarding_callback, name='infrastructure-onboarding-callback'),
    path('infrastructures/<str:infra_id>/users/<str:user_id>/', infrastructure_remove_user, name='infrastructure-remove-user'),
    path('healthz/', health, name='health'),
    path('liveness/', liveness, name='liveness'),
    path('readiness/', readiness, name='readiness'),
    path('aws/regions/', list_aws_regions, name='aws-regions'),
    path('capabilities/', list_capabilities, name='capabilities'),
]
