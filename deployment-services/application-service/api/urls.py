from django.urls import path
from api.views.application import (
    ApplicationListCreateView, 
    ApplicationDetailDeleteView,
    ApplicationUpdateView,
    ApplicationDeployView,
    ApplicationRetryDeployView,
    ApplicationSleepView,
    ApplicationWakeView
)
from api.views.infrastructure_validation import infrastructure_validation
from api.views.health import health_check, liveness_check, readiness_check
from api.views.metrics import metrics_view

urlpatterns = [
    path('applications/', ApplicationListCreateView.as_view(), name='application-list-create'),
    path('applications/<uuid:pk>/', ApplicationDetailDeleteView.as_view(), name='application-detail-delete'),
    path('applications/<uuid:pk>/update/', ApplicationUpdateView.as_view(), name='application-update'),
    path('applications/<uuid:pk>/deploy/', ApplicationDeployView.as_view(), name='application-deploy'),
    path('applications/<uuid:pk>/retry/', ApplicationRetryDeployView.as_view(), name='application-retry-deploy'),
    path('applications/<uuid:pk>/sleep/', ApplicationSleepView.as_view(), name='application-sleep'),
    path('applications/<uuid:pk>/wake/', ApplicationWakeView.as_view(), name='application-wake'),
    path('infrastructures/<uuid:infra_id>/validation/', infrastructure_validation, name='infrastructure-validation'),
    path('healthz/', health_check, name='health'),
    path('liveness/', liveness_check, name='liveness'),
    path('readiness/', readiness_check, name='readiness'),
    path('metrics/', metrics_view, name='metrics'),
]
