from api.services.health_service import HealthService
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.http import HttpRequest
from django.views.decorators.csrf import csrf_exempt

healthService = HealthService()

@csrf_exempt
@api_view(['GET'])
@permission_classes([AllowAny])
def health(request: HttpRequest):
    if request.method == 'GET':
        return healthService.get_health()

@csrf_exempt
@api_view(['GET'])
@permission_classes([AllowAny])
def liveness(request: HttpRequest):
    if request.method == 'GET':
        return healthService.get_liveness()

@csrf_exempt
@api_view(['GET'])
@permission_classes([AllowAny])
def readiness(request: HttpRequest):
    if request.method == 'GET':
        return healthService.get_readiness()
