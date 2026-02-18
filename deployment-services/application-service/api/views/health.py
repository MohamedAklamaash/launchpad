from api.services.health_service import HealthService
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.http import HttpRequest
from django.views.decorators.csrf import csrf_exempt

healthService = HealthService()

@csrf_exempt
@api_view(['GET'])
def health(request: HttpRequest):
    if request.method == 'GET':
        return healthService.get_health()

@api_view(['GET'])
def liveness(request: HttpRequest):
    if request.method == 'GET':
        return healthService.get_liveness()

@api_view(['GET'])
def readiness(request: HttpRequest):
    if request.method == 'GET':
        return healthService.get_readiness()
