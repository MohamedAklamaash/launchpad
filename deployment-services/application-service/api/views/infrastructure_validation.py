from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from api.services.infrastructure_validation import InfrastructureValidation


@api_view(['GET'])
def infrastructure_validation(request, infra_id):
    """Check if infrastructure can be deleted"""
    can_delete, error_message = InfrastructureValidation.can_delete_infrastructure(infra_id)
    app_count = InfrastructureValidation.get_infrastructure_apps_count(infra_id)
    
    return Response({
        "can_delete": can_delete,
        "app_count": app_count,
        "error_message": error_message
    })
