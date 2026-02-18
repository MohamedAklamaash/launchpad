from django.urls import path
from api.views.application import ApplicationListCreateView, ApplicationDetailDeleteView

urlpatterns = [
    path('applications/', ApplicationListCreateView.as_view(), name='application-list-create'),
    path('applications/<uuid:pk>/', ApplicationDetailDeleteView.as_view(), name='application-detail-delete'),
]
