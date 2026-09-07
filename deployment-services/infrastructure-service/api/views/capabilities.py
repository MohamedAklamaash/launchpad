"""What the platform will actually accept, so the dashboard can stop offering options
the server is configured to reject.

Authenticated on purpose: this reports platform configuration, and there is no reason to
disclose it to anonymous callers the way the static AWS region list is.
"""
from django.conf import settings
from rest_framework.decorators import api_view
from rest_framework.response import Response
from shared.enums.orchestrator import ComputeType


@api_view(['GET'])
def list_capabilities(request):
    return Response({
        "compute_types": [
            {
                "value": ComputeType.ECS_FARGATE,
                "label": "ECS Fargate",
                "enabled": True,
            },
            {
                "value": ComputeType.EKS,
                "label": "Kubernetes",
                # EKS_ENABLED is re-checked at create and again at provision dispatch;
                # this only stops the dashboard offering a target that would 400.
                "enabled": bool(settings.EKS_ENABLED),
            },
        ],
    })
