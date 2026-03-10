from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt

# Deployment metrics
deployment_counter = Counter(
    'deployments_total',
    'Total number of deployments',
    ['status', 'application']
)

deployment_duration = Histogram(
    'deployment_duration_seconds',
    'Deployment duration in seconds',
    ['application']
)

active_deployments = Gauge(
    'active_deployments',
    'Number of currently active deployments'
)

deployment_failures = Counter(
    'deployment_failures_total',
    'Total number of deployment failures',
    ['error_type', 'application']
)

@csrf_exempt
@require_http_methods(["GET"])
def metrics_view(request):
    """Prometheus metrics endpoint"""
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)
