from django.urls import path

from api.views.app_metrics import application_metrics
from api.views.application import (
    ApplicationDeploymentsView,
    ApplicationDeployView,
    ApplicationDetailDeleteView,
    ApplicationListCreateView,
    ApplicationResumeAutoDeployView,
    ApplicationRetryDeployView,
    ApplicationRollbackPreviewView,
    ApplicationRollbackView,
    ApplicationSleepView,
    ApplicationUpdateView,
    ApplicationWakeView,
    application_github_webhook,
    application_rotate_webhook_secret,
)
from api.views.custom_domains_internal import (
    application_summary_for_custom_domains,
    custom_domain_attach,
    custom_domain_detach,
)
from api.views.exit_inventory import export_inventory
from api.views.health import health_check, liveness_check, readiness_check
from api.views.infrastructure_validation import infrastructure_validation
from api.views.metrics import metrics_view
from api.views.runtime_logs import runtime_logs

urlpatterns = [
    path('applications/', ApplicationListCreateView.as_view(), name='application-list-create'),
    path('applications/<uuid:pk>/', ApplicationDetailDeleteView.as_view(), name='application-detail-delete'),
    path('applications/<uuid:pk>/update/', ApplicationUpdateView.as_view(), name='application-update'),
    path('applications/<uuid:pk>/deploy/', ApplicationDeployView.as_view(), name='application-deploy'),
    path('applications/<uuid:pk>/retry/', ApplicationRetryDeployView.as_view(), name='application-retry-deploy'),
    path('applications/<uuid:pk>/sleep/', ApplicationSleepView.as_view(), name='application-sleep'),
    path('applications/<uuid:pk>/wake/', ApplicationWakeView.as_view(), name='application-wake'),
    path('applications/<uuid:pk>/deployments/', ApplicationDeploymentsView.as_view(), name='application-deployments'),
    path('applications/<uuid:pk>/deployments/<uuid:deployment_id>/preview/', ApplicationRollbackPreviewView.as_view(), name='application-rollback-preview'),
    path('applications/<uuid:pk>/deployments/<uuid:deployment_id>/rollback/', ApplicationRollbackView.as_view(), name='application-rollback'),
    path('applications/<uuid:pk>/resume-auto-deploy/', ApplicationResumeAutoDeployView.as_view(), name='application-resume-auto-deploy'),
    path('applications/<uuid:app_id>/webhook-secret/', application_rotate_webhook_secret, name='application-webhook-secret'),
    path('webhooks/github/<uuid:app_id>/', application_github_webhook, name='application-github-webhook'),
    path('infrastructures/<uuid:infra_id>/validation/', infrastructure_validation, name='infrastructure-validation'),
    # Internal-only: no gateway route exists for this path. Called same-origin by
    # infrastructure-service's exit-export endpoint (F6).
    path('infrastructures/<uuid:infra_id>/export-inventory/', export_inventory, name='infrastructure-export-inventory'),
    # Internal-only, fixed literal paths (no gateway route, no path-param UUID — see
    # shared/middleware/authentication.py's EXEMPT_EXACT_PATHS, which matches by exact
    # string). Called same-origin by infrastructure-service (F1b part 3b).
    path('internal/custom-domains/attach/', custom_domain_attach, name='custom-domain-attach'),
    path('internal/custom-domains/detach/', custom_domain_detach, name='custom-domain-detach'),
    # Owner-only (forwards the caller's JWT, unlike the two above) — a narrow
    # {id, infrastructure_id, status} lookup for infrastructure-service's claim flow, so
    # it never receives a full application detail (env vars, etc.) it has no use for.
    path('internal/applications/<uuid:app_id>/summary/', application_summary_for_custom_domains,
         name='application-summary-for-custom-domains'),
    path('healthz/', health_check, name='health'),
    path('liveness/', liveness_check, name='liveness'),
    path('readiness/', readiness_check, name='readiness'),
    path('metrics/', metrics_view, name='metrics'),
    # F2: runtime log tailing — appended at the end to keep this file's diff additive.
    path('applications/<uuid:app_id>/logs/', runtime_logs, name='application-runtime-logs'),
    # Per-app dashboard metrics. Distinct from `metrics/` above (that's the Prometheus
    # scrape endpoint at the service root, not per-application).
    path('applications/<uuid:app_id>/metrics/', application_metrics, name='application-metrics'),
]
