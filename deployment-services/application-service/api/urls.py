from django.urls import path
from api.views.application import ApplicationListCreateView, ApplicationDetailDeleteView
from api.views.health import health, liveness, readiness

urlpatterns = [
    path('applications/', ApplicationListCreateView.as_view(), name='application-list-create'),
    path('applications/<uuid:pk>/', ApplicationDetailDeleteView.as_view(), name='application-detail-delete'),
    path('healthz/', health, name='health'),
    path('liveness/', liveness, name='liveness'),
    path('readiness/', readiness, name='readiness'),
]
