from django.urls import path
from api.views.infrastructure import infrastructure_list_create, infrastructure_detail

urlpatterns = [
    path('infrastructures/', infrastructure_list_create, name='infrastructure-list-create'),
    path('infrastructures/<uuid:infra_id>/', infrastructure_detail, name='infrastructure-detail'),
]
