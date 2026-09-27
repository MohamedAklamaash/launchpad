from django.urls import path

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
from api.views.health import health_check, liveness_check, readiness_check
from api.views.infrastructure_validation import infrastructure_validation
from api.views.metrics import metrics_view

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
    path('healthz/', health_check, name='health'),
    path('liveness/', liveness_check, name='liveness'),
    path('readiness/', readiness_check, name='readiness'),
    path('metrics/', metrics_view, name='metrics'),
]
