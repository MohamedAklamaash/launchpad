import logging

from api.services.cfn_onboarding import (
    CfnOnboardingForbidden,
    CfnOnboardingMockUnsupported,
    CfnOnboardingNotCloudFormation,
    CfnOnboardingNotFound,
    CfnOnboardingRoleNotTagged,
    CfnOnboardingService,
    CfnOnboardingUnavailable,
)
from api.services.onboarding import (
    OnboardingAssumeRoleFailed,
    OnboardingAssumeRoleValueError,
    OnboardingFinishFailed,
    OnboardingTokenClaimFailed,
)
from django.conf import settings
from django.http import HttpRequest
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from shared.ratelimit import rate_limited

logger = logging.getLogger(__name__)

cfn_onboarding_service = CfnOnboardingService()


def _not_found():
    return Response({'error': 'Infrastructure not found'}, status=status.HTTP_404_NOT_FOUND)


def _error_response(e: Exception):
    # Dedicated exception types dispatched by class, never str(e) for an AWS-facing
    # failure — an AssumeRole/ListRoleTags exception message can carry a role ARN.
    if isinstance(e, CfnOnboardingNotFound):
        return _not_found()
    if isinstance(e, CfnOnboardingForbidden):
        return Response(
            {'error': 'Only the infrastructure owner can do this'}, status=status.HTTP_403_FORBIDDEN,
        )
    if isinstance(e, CfnOnboardingMockUnsupported):
        return Response(
            {'error': 'CloudFormation onboarding is not available for mock infrastructures'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if isinstance(e, CfnOnboardingNotCloudFormation):
        return Response(
            {'error': 'Infrastructure was not onboarded via CloudFormation'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if isinstance(e, CfnOnboardingRoleNotTagged):
        return Response(
            {'error': 'LaunchpadDeploymentRole is not tagged as CloudFormation-managed. '
                      'Launch the CloudFormation stack before verifying the connection.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if isinstance(e, OnboardingTokenClaimFailed):
        return Response({'error': 'Onboarding token already used'}, status=status.HTTP_403_FORBIDDEN)
    if isinstance(e, OnboardingAssumeRoleValueError):
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(e, OnboardingAssumeRoleFailed):
        response = Response(
            {'error': 'AssumeRole failed', 'details': e.exc_type_name, 'retry_after_seconds': 30},
            status=status.HTTP_403_FORBIDDEN,
        )
        response['Retry-After'] = '30'
        return response
    if isinstance(e, (OnboardingFinishFailed, CfnOnboardingUnavailable)):
        logger.error("cfn onboarding call failed: %s", type(e).__name__)
        return Response({'error': 'Internal error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    logger.exception("Unhandled cfn onboarding error", exc_info=e)
    return Response({'error': 'Internal error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@rate_limited(
    'cfn_onboarding', limit=settings.RATE_BUDGET_CFN_ONBOARDING_LIMIT,
    window=settings.RATE_BUDGET_CFN_ONBOARDING_WINDOW_SECONDS,
)
def _cfn_verify(request: HttpRequest, infra_id):
    try:
        result = cfn_onboarding_service.verify(user_id=request.user.id, infra_id=infra_id)
    except Exception as e:
        return _error_response(e)
    status_code = status.HTTP_200_OK if result['status'] == 'already_queued' else status.HTTP_202_ACCEPTED
    return Response(result, status=status_code)


@extend_schema(
    summary="Verify a CloudFormation-onboarded connection",
    description=(
        "Owner only. Claims this infra's onboarding token, AssumeRoles into the customer's "
        "account, and confirms LaunchpadDeploymentRole is tagged as CloudFormation-managed. "
        "On success, writes policy_version/cfn_stack_name onto every CloudFormation-onboarded "
        "sibling sharing this AWS account and starts provisioning — the GUI-path equivalent of "
        "the script's onboarding callback. 404 when CFN_ONBOARDING_ENABLED is off."
    ),
    parameters=[OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH)],
    request=None,
    responses={
        202: OpenApiTypes.OBJECT, 200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT,
        403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT, 500: OpenApiTypes.OBJECT,
    },
)
@csrf_exempt
@api_view(['POST'])
def infrastructure_cfn_verify(request: HttpRequest, infra_id):
    if not settings.CFN_ONBOARDING_ENABLED:
        return _not_found()
    return _cfn_verify(request, infra_id)


@rate_limited(
    'cfn_onboarding', limit=settings.RATE_BUDGET_CFN_ONBOARDING_LIMIT,
    window=settings.RATE_BUDGET_CFN_ONBOARDING_WINDOW_SECONDS,
)
def _cfn_refresh(request: HttpRequest, infra_id):
    try:
        result = cfn_onboarding_service.refresh(user_id=request.user.id, infra_id=infra_id)
    except Exception as e:
        return _error_response(e)
    return Response(result, status=status.HTTP_200_OK)


@extend_schema(
    summary="Start a policy refresh for a CloudFormation-onboarded infrastructure",
    description=(
        "Owner only. AssumeRoles into the already-onboarded customer account, selects the "
        "widest-compute-type template across every authenticated sibling sharing this AWS "
        "account (never-downgrade), and calls CreateChangeSet. Returns a console review link "
        "— the customer reviews and executes the change set themselves, same posture as the "
        "script's refresh flow. 404 when CFN_ONBOARDING_ENABLED is off."
    ),
    parameters=[OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH)],
    request=None,
    responses={
        200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT,
        404: OpenApiTypes.OBJECT, 500: OpenApiTypes.OBJECT,
    },
)
@csrf_exempt
@api_view(['POST'])
def infrastructure_cfn_refresh(request: HttpRequest, infra_id):
    if not settings.CFN_ONBOARDING_ENABLED:
        return _not_found()
    return _cfn_refresh(request, infra_id)
