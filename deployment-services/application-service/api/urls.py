from django.urls import path
from api.views.application import (
    ApplicationListCreateView, 
    ApplicationDetailDeleteView, 
    ApplicationDeployView,
    ApplicationRetryDeployView
)
from api.views.health import health, liveness, readiness

urlpatterns = [
    path('applications/', ApplicationListCreateView.as_view(), name='application-list-create'),
    path('applications/<uuid:pk>/', ApplicationDetailDeleteView.as_view(), name='application-detail-delete'),
    path('applications/<uuid:pk>/deploy/', ApplicationDeployView.as_view(), name='application-deploy'),
    path('applications/<uuid:pk>/retry/', ApplicationRetryDeployView.as_view(), name='application-retry-deploy'),
    path('healthz/', health, name='health'),
    path('liveness/', liveness, name='liveness'),
    path('readiness/', readiness, name='readiness'),
]
