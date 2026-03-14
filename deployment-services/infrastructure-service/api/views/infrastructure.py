from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from api.services.infrastructure import InfrastructureService
from django.http import HttpRequest

infrastructure_service = InfrastructureService()

@csrf_exempt
@api_view(['GET', 'POST'])
def infrastructure_list_create(request: HttpRequest):
    if request.method == 'GET':
        data = infrastructure_service.get_all_for_user(user_id=request.user.id)
        return Response(data)
    
    elif request.method == 'POST':
        try:
            infra = infrastructure_service.create_infrastructure(user_id=request.user.id, infra_data=request.data)
            return Response(infra, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'error': f"Failed to authenticate cloud provider: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

@csrf_exempt
@api_view(['GET', 'PUT', 'DELETE'])
def infrastructure_detail(request: HttpRequest, infra_id):
    if request.method == 'GET':
        infra = infrastructure_service.get_infrastructure(user_id=request.user.id, infra_id=infra_id)
        if infra:
            return Response(infra)
        return Response({'error': 'Infrastructure not found'}, status=status.HTTP_404_NOT_FOUND)
    
    elif request.method == 'DELETE':
        try:
            success = infrastructure_service.delete_infrastructure(user_id=request.user.id, infra_id=infra_id)
            if success:
                return Response(status=status.HTTP_204_NO_CONTENT)
            return Response({'error': 'Infrastructure not found'}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)

@csrf_exempt
@api_view(['PATCH'])
def infrastructure_update(request: HttpRequest, infra_id):
    """Update infrastructure configuration (name, max_cpu, max_memory)."""
    try:
        infra = infrastructure_service.update_infrastructure_config(
            user_id=request.user.id, 
            infra_id=infra_id, 
            update_data=request.data
        )
        if infra:
            return Response(infra)
        return Response({'error': 'Infrastructure not found'}, status=status.HTTP_404_NOT_FOUND)
    except ValueError as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@csrf_exempt
@api_view(['DELETE'])
def infrastructure_remove_user(request: HttpRequest, infra_id, user_id):
    """Remove an invited user from an infrastructure (owner only)."""
    try:
        success = infrastructure_service.remove_invited_user(
            owner_id=request.user.id,
            infra_id=infra_id,
            target_user_id=user_id,
        )
        if success:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response({'error': 'Infrastructure not found'}, status=status.HTTP_404_NOT_FOUND)
    except PermissionError as e:
        return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)