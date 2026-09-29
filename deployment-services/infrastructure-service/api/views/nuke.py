import logging

from api.services.nuke_service import NukeService
from django.http import HttpRequest
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

logger = logging.getLogger(__name__)

nuke_service = NukeService()


class NukeStartSerializer(serializers.Serializer):
    confirm_name = serializers.CharField(help_text="Must exactly match the infrastructure's current name")


class NukeStepSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    status = serializers.ChoiceField(choices=["pending", "running", "success", "failed", "policy_refresh_required"])
    detail = serializers.JSONField(allow_null=True)


class NukeLeftoverSerializer(serializers.Serializer):
    type = serializers.CharField()
    id = serializers.CharField()
    reason = serializers.CharField()


class NukeStatusSerializer(serializers.Serializer):
    infrastructure_id = serializers.CharField()
    status = serializers.ChoiceField(choices=["PENDING", "RUNNING", "FAILED", "COMPLETED"])
    steps = NukeStepSerializer(many=True)
    leftovers = NukeLeftoverSerializer(many=True)
    started_at = serializers.DateTimeField(allow_null=True)
    finished_at = serializers.DateTimeField(allow_null=True)


class ErrorSerializer(serializers.Serializer):
    error = serializers.CharField()


@extend_schema(
    summary="Start a Nuke infrastructure run",
    description=(
        "Owner-only. Destroys every resource Launchpad created for this infrastructure in the "
        "customer's AWS account — applications, databases (no final snapshot), custom domains, "
        "Terraform-managed infrastructure, and out-of-terraform leftovers — then verifies nothing "
        "is left. Unlike DELETE, does not refuse when applications or databases exist; it deletes "
        "them as part of the run. Requires typed-name confirmation. 409 if a run is already in "
        "progress; retrying a FAILED run resumes it, skipping steps that already succeeded."
    ),
    parameters=[OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH)],
    request=NukeStartSerializer,
    responses={202: NukeStatusSerializer, 403: ErrorSerializer, 404: ErrorSerializer, 409: ErrorSerializer},
    methods=["POST"],
)
@extend_schema(
    summary="Get the status of the current (or most recent) Nuke infrastructure run",
    description=(
        "Owner-only. Still answers after a COMPLETED run has deleted the Infrastructure row — the "
        "run record itself outlives it."
    ),
    parameters=[OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH)],
    responses={200: NukeStatusSerializer, 403: ErrorSerializer, 404: ErrorSerializer},
    methods=["GET"],
)
@csrf_exempt
@api_view(['GET', 'POST'])
def infrastructure_nuke(request: HttpRequest, infra_id):
    try:
        if request.method == 'GET':
            result = nuke_service.get_status(user_id=request.user.id, infra_id=infra_id)
            if result is None:
                return Response({'error': 'Infrastructure not found'}, status=status.HTTP_404_NOT_FOUND)
            return Response(result, status=status.HTTP_200_OK)

        body = NukeStartSerializer(data=request.data)
        if not body.is_valid():
            return Response({'error': 'Invalid request', 'details': body.errors}, status=status.HTTP_400_BAD_REQUEST)
        result = nuke_service.start_nuke(
            user_id=request.user.id, infra_id=infra_id, confirm_name=body.validated_data['confirm_name'],
        )
        if result is None:
            return Response({'error': 'Infrastructure not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response(result, status=status.HTTP_202_ACCEPTED)
    except PermissionError as e:
        return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)
    except ValueError as e:
        return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
