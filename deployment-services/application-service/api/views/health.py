from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.db import connection
from api.services.deployment_queue import DeploymentQueue

@csrf_exempt
@require_http_methods(["GET"])
def health_check(request):
    """Basic health check endpoint"""
    return JsonResponse({'status': 'healthy', 'service': 'application-service'})

@csrf_exempt
@require_http_methods(["GET"])
def readiness_check(request):
    """Readiness check - verifies dependencies are available"""
    try:
        connection.ensure_connection()
        DeploymentQueue.redis_client.ping()
        
        return JsonResponse({
            'status': 'ready',
            'service': 'application-service',
            'checks': {
                'database': 'ok',
                'redis': 'ok'
            }
        })
    except Exception as e:
        return JsonResponse({
            'status': 'not ready',
            'service': 'application-service',
            'error': str(e)
        }, status=503)

@csrf_exempt
@require_http_methods(["GET"])
def liveness_check(request):
    """Liveness check - verifies service is running"""
    return JsonResponse({'status': 'alive', 'service': 'application-service'})
