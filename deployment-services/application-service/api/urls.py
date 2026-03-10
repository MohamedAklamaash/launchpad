from django.urls import path
from api.views.application import (
    ApplicationListCreateView, 
    ApplicationDetailDeleteView, 
    ApplicationDeployView,
    ApplicationRetryDeployView
)
from api.views.health import health_check, liveness_check, readiness_check
from api.views.metrics import metrics_view

urlpatterns = [
    path('applications/', ApplicationListCreateView.as_view(), name='application-list-create'),
    path('applications/<uuid:pk>/', ApplicationDetailDeleteView.as_view(), name='application-detail-delete'),
    path('applications/<uuid:pk>/deploy/', ApplicationDeployView.as_view(), name='application-deploy'),
    path('applications/<uuid:pk>/retry/', ApplicationRetryDeployView.as_view(), name='application-retry-deploy'),
    path('healthz/', health_check, name='health'),
    path('liveness/', liveness_check, name='liveness'),
    path('readiness/', readiness_check, name='readiness'),
    path('metrics/', metrics_view, name='metrics'),
]
