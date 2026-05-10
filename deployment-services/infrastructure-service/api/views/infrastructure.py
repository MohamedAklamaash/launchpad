import logging
import secrets
import time
import uuid

from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework import status, serializers
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from api.services.infrastructure import InfrastructureService
from django.http import HttpRequest

logger = logging.getLogger(__name__)

infrastructure_service = InfrastructureService()


# ── Serializers ───────────────────────────────────────────────────────────────

class InfraResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    cloud_provider = serializers.CharField(help_text="e.g. AWS")
    max_cpu = serializers.FloatField(help_text="Total CPU units ceiling (1024 = 1 vCPU)")
    max_memory = serializers.FloatField(help_text="Total memory ceiling in MB")
    is_cloud_authenticated = serializers.BooleanField(help_text="Whether Launchpad successfully assumed the IAM role")
    code = serializers.CharField(help_text="AWS Account ID")
    metadata = serializers.DictField(child=serializers.CharField(), help_text='e.g. {"aws_region":"us-east-1"}')
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class InfraCreateResponseSerializer(InfraResponseSerializer):
    # Plaintext single-use nonce, returned only on the create response. The dashboard injects this
    # into the bootstrap script so the onboarding callback can authenticate without a JWT.
    onboarding_token = serializers.CharField(help_text="Single-use onboarding token; shown only once")

class InfraCreateSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="Human-readable name, e.g. prod-infra")
    cloud_provider = serializers.ChoiceField(choices=["AWS"], help_text="Only AWS is supported")
    max_cpu = serializers.FloatField(help_text="Total CPU units to allocate across all apps (1024 = 1 vCPU)")
    max_memory = serializers.FloatField(help_text="Total memory in MB to allocate across all apps")
    code = serializers.CharField(help_text="AWS Account ID where infrastructure will be provisioned, e.g. 123456789012")
    metadata = serializers.DictField(
        child=serializers.CharField(), required=False,
        help_text='Optional AWS config, e.g. {"aws_region":"us-east-1","vpc_cidr":"10.0.0.0/16"}'
    )

class InfraUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(required=False)
    max_cpu = serializers.FloatField(required=False, help_text="New CPU units ceiling")
    max_memory = serializers.FloatField(required=False, help_text="New memory ceiling in MB")

class ErrorSerializer(serializers.Serializer):
    error = serializers.CharField()


# ── Views ─────────────────────────────────────────────────────────────────────

@extend_schema(
    summary="List all infrastructures for the authenticated user",
    description="Returns infrastructures the user owns or has been invited to.",
    responses={200: InfraResponseSerializer(many=True)},
    methods=["GET"],
)
@extend_schema(
    summary="Create a new infrastructure",
    description=(
        "Creates the infra row and mints a single-use onboarding token. The customer must run the "
        "AWS bootstrap script (with the token injected) before provisioning will start."
    ),
    request=InfraCreateSerializer,
    responses={201: InfraCreateResponseSerializer, 400: ErrorSerializer},
    methods=["POST"],
)
@csrf_exempt
@api_view(['GET', 'POST'])
def infrastructure_list_create(request: HttpRequest):
    if request.method == 'GET':
        return Response(infrastructure_service.get_all_for_user(user_id=request.user.id))
    try:
        infra = infrastructure_service.create_infrastructure(user_id=request.user.id, infra_data=request.data)
        return Response(infra, status=status.HTTP_201_CREATED)
    except ValueError as e:
        # ValueError is raised explicitly by the service layer for bad user input (cloud provider, account ID).
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception:
        # Bare Exception was masking DB/RabbitMQ/programming errors as AWS auth failures, sending ops on
        # ghost IAM hunts and hiding the incident from 5xx alerts. Surface real failure as a 500 with the
        # stack trace going to logs (not the response).
        logger.error(f"create_infrastructure failed for user {request.user.id}", exc_info=True)
        return Response({'error': 'Internal error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    summary="Get infrastructure details",
    parameters=[OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH, description="Infrastructure UUID")],
    responses={200: InfraResponseSerializer, 404: ErrorSerializer},
    methods=["GET"],
)
@extend_schema(
    summary="Delete an infrastructure",
    description="Triggers Terraform destroy to tear down VPC, ECS, ALB, ECR. Returns 409 if active applications exist.",
    parameters=[OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH, description="Infrastructure UUID")],
    responses={204: None, 403: ErrorSerializer, 404: ErrorSerializer, 409: ErrorSerializer},
    methods=["DELETE"],
)
@csrf_exempt
@api_view(['GET', 'DELETE'])
def infrastructure_detail(request: HttpRequest, infra_id):
    if request.method == 'GET':
        infra = infrastructure_service.get_infrastructure(user_id=request.user.id, infra_id=infra_id)
        if infra:
            return Response(infra)
        return Response({'error': 'Infrastructure not found'}, status=status.HTTP_404_NOT_FOUND)
    try:
        success = infrastructure_service.delete_infrastructure(user_id=request.user.id, infra_id=infra_id)
        if success:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response({'error': 'Infrastructure not found'}, status=status.HTTP_404_NOT_FOUND)
    except PermissionError as e:
        return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)
    except ValueError as e:
        return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)


@extend_schema(
    summary="Update infrastructure configuration",
    description="Partial update — only send fields you want to change. Does not re-provision AWS resources.",
    parameters=[OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH, description="Infrastructure UUID")],
    request=InfraUpdateSerializer,
    responses={200: InfraResponseSerializer, 400: ErrorSerializer, 404: ErrorSerializer},
)
@csrf_exempt
@api_view(['PATCH'])
def infrastructure_update(request: HttpRequest, infra_id):
    try:
        infra = infrastructure_service.update_infrastructure_config(
            user_id=request.user.id, infra_id=infra_id, update_data=request.data
        )
        if infra:
            return Response(infra)
        return Response({'error': 'Infrastructure not found'}, status=status.HTTP_404_NOT_FOUND)
    except ValueError as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    summary="Re-provision an infrastructure (re-run Terraform)",
    description="Resets the environment status to PENDING and re-queues a Terraform provision job. Use when provisioning failed or to apply config changes.",
    parameters=[OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH)],
    request=None,
    responses={202: {"type": "object", "properties": {"message": {"type": "string"}}},
               404: {"type": "object", "properties": {"error": {"type": "string"}}},
               409: {"type": "object", "properties": {"error": {"type": "string"}}}},
)
@csrf_exempt
@api_view(['POST'])
def infrastructure_reprovision(request: HttpRequest, infra_id):
    from api.models.environment import Environment
    from api.services.infra_queue import InfraQueue

    infra = infrastructure_service.get_infrastructure(user_id=request.user.id, infra_id=infra_id)
    if not infra:
        return Response({'error': 'Infrastructure not found'}, status=status.HTTP_404_NOT_FOUND)
    try:
        env = Environment.objects.get(infrastructure_id=infra_id)
        if env.status in ('PROVISIONING', 'DESTROYING'):
            return Response({'error': f'Cannot reprovision while status is {env.status}'},
                            status=status.HTTP_409_CONFLICT)
        env.status = 'PENDING'
        env.error_message = None
        env.save(update_fields=['status', 'error_message'])
    except Environment.DoesNotExist:
        from api.models.infrastructure import Infrastructure as InfraModel
        infra_obj = InfraModel.objects.get(id=infra_id)
        Environment.objects.create(infrastructure=infra_obj, status='PENDING')

    InfraQueue.enqueue_provision(str(infra_id))
    return Response({'message': 'Re-provisioning queued'}, status=status.HTTP_202_ACCEPTED)


@extend_schema(
    summary="Onboarding callback from customer's AWS account",
    description=(
        "Called by app_scripts/create_aws_role.sh after the customer creates LaunchpadDeploymentRole in "
        "their AWS account. Authenticated by a single-use onboarding token minted at infra creation; on "
        "success persists STS credentials and enqueues provisioning."
    ),
    request={
        "type": "object",
        "required": ["infra_id", "account_id", "onboarding_token"],
        "properties": {
            "infra_id": {"type": "string", "format": "uuid"},
            "account_id": {"type": "string", "description": "AWS Account ID, must match infra.code"},
            "onboarding_token": {"type": "string", "description": "Single-use token issued at infra creation"},
        },
    },
    responses={
        202: {"type": "object", "properties": {
            "status": {"type": "string"},
            "infrastructure_id": {"type": "string"},
            "is_cloud_authenticated": {"type": "boolean"},
        }},
        400: ErrorSerializer, 403: ErrorSerializer, 404: ErrorSerializer, 500: ErrorSerializer,
    },
)
@csrf_exempt
@api_view(['POST'])
@permission_classes([AllowAny])
def infrastructure_onboarding_callback(request: HttpRequest):
    from api.models.infrastructure import Infrastructure as InfraModel
    from api.cloud_providers.aws.authenticate import authenticate_infrastructure
    from api.services.infra_queue import InfraQueue
    from shared.enums.cloud_provider import CloudProvider

    infra_id = request.data.get('infra_id')
    account_id = request.data.get('account_id')
    if not infra_id or not account_id:
        return Response({'error': 'infra_id and account_id are required'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        uuid.UUID(str(infra_id))
    except (ValueError, TypeError):
        return Response({'error': 'infra_id must be a valid UUID'},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        infra = InfraModel.objects.get(id=infra_id)
    except InfraModel.DoesNotExist:
        return Response({'error': 'Infrastructure not found'},
                        status=status.HTTP_404_NOT_FOUND)

    if str(infra.code) != str(account_id):
        # Mismatch means the script ran in a different AWS account than the one the user registered.
        return Response({'error': 'Account ID mismatch'},
                        status=status.HTTP_403_FORBIDDEN)

    if infra.cloud_provider != CloudProvider.AWS:
        return Response({'error': 'Not an AWS infrastructure'},
                        status=status.HTTP_400_BAD_REQUEST)

    provided_token = request.data.get('onboarding_token')
    if not provided_token:
        return Response({'error': 'onboarding_token is required'}, status=status.HTTP_400_BAD_REQUEST)
    if not infra.onboarding_token_hash:
        return Response({'error': 'No onboarding token issued for this infrastructure'},
                        status=status.HTTP_403_FORBIDDEN)
    if infra.onboarding_token_used_at is not None:
        return Response({'error': 'Onboarding token already used'}, status=status.HTTP_403_FORBIDDEN)
    expected_hash = InfraModel.hash_token(provided_token)
    # Constant-time comparison defends against timing oracles on the hash check.
    if not secrets.compare_digest(expected_hash, infra.onboarding_token_hash):
        return Response({'error': 'Invalid onboarding token'}, status=status.HTTP_403_FORBIDDEN)

    # Bounded retry around AssumeRole: IAM role/policy creation in the customer account is eventually
    # consistent, so the first call can race the script's policy-attach step.
    last_exc = None
    for attempt in range(3):
        try:
            authenticate_infrastructure(infra)
            last_exc = None
            break
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            last_exc = e
            logger.warning(
                f"Onboarding callback AssumeRole attempt {attempt + 1}/3 failed for infra {infra_id}: {e}"
            )
            if attempt < 2:
                time.sleep(5)

    if last_exc is not None:
        response = Response({'error': 'AssumeRole failed'}, status=status.HTTP_403_FORBIDDEN)
        response['Retry-After'] = '30'
        return response

    # Burn the token only after AssumeRole succeeds — a failed attempt should be retryable.
    infra.onboarding_token_used_at = timezone.now()
    infra.save(update_fields=['onboarding_token_used_at'])

    try:
        logger.info(f"onboarding callback succeeded for infra {infra_id}")
        was_enqueued = InfraQueue.enqueue_provision(str(infra_id))
        if not was_enqueued:
            return Response(
                {'status': 'already_queued', 'infrastructure_id': str(infra_id), 'is_cloud_authenticated': True},
                status=status.HTTP_200_OK,
            )
        return Response(
            {'status': 'accepted', 'infrastructure_id': str(infra_id), 'is_cloud_authenticated': True},
            status=status.HTTP_202_ACCEPTED,
        )
    except Exception as e:
        logger.error(f"Onboarding callback enqueue failed for infra {infra_id}", exc_info=True)
        return Response({'error': 'Internal error'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    summary="Remove an invited user from infrastructure",
    description="Owner only. Removes the target user from the infrastructure's invited_users list.",
    parameters=[
        OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH, description="Infrastructure UUID"),
        OpenApiParameter("user_id", OpenApiTypes.STR, OpenApiParameter.PATH, description="UUID of the user to remove"),
    ],
    request=None,
    responses={204: None, 403: ErrorSerializer, 404: ErrorSerializer, 400: ErrorSerializer},
)
@csrf_exempt
@api_view(['DELETE'])
def infrastructure_remove_user(request: HttpRequest, infra_id, user_id):
    try:
        success = infrastructure_service.remove_invited_user(
            owner_id=request.user.id, infra_id=infra_id, target_user_id=user_id,
        )
        if success:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response({'error': 'Infrastructure not found'}, status=status.HTTP_404_NOT_FOUND)
    except PermissionError as e:
        return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
