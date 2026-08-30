import logging
import uuid

from django.http import HttpRequest
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

logger = logging.getLogger(__name__)


class ScriptApiKeyResponseSerializer(serializers.Serializer):
    api_key = serializers.CharField(help_text="Per-user script API key (prefix lp_); shown only once")


class PolicyRefreshCallbackResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    event_id = serializers.UUIDField()


class CallbackErrorSerializer(serializers.Serializer):
    error = serializers.CharField()


@extend_schema(
    summary="Issue (or rotate) the per-user script API key",
    description=(
        "Mints the API key that authenticates customer-run refreshes (create_aws_role.sh with a "
        "script API key) back to Launchpad via the policy-refresh callback. Only a hash is stored; the plaintext is "
        "returned exactly once. Issuing again revokes all previously issued keys for the user."
    ),
    request=None,
    responses={201: ScriptApiKeyResponseSerializer, 404: CallbackErrorSerializer},
)
@csrf_exempt
@api_view(['POST'])
def script_api_key_issue(request: HttpRequest):
    from api.models.script_api_key import ScriptApiKey
    from api.models.user import User

    try:
        user = User.objects.get(id=request.user.id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

    plaintext = ScriptApiKey.issue(user)
    return Response({'api_key': plaintext}, status=status.HTTP_201_CREATED)


@extend_schema(
    summary="Policy-refresh callback from customer's AWS account",
    description=(
        "Called by app_scripts/create_aws_role.sh after an attributed refresh of LaunchpadDeploymentPolicy / "
        "the trust policy. Authenticated by the per-user script API key (X-API-Key header) so the "
        "platform records WHO ran the refresh, against which AWS account, and when."
    ),
    request={
        "type": "object",
        "required": ["account_id"],
        "properties": {
            "infra_id": {"type": "string", "format": "uuid", "description": "Optional infra UUID to link"},
            "account_id": {"type": "string", "description": "AWS Account ID the script ran against"},
            "caller_arn": {"type": "string", "description": "sts get-caller-identity ARN of whoever ran it"},
            "script": {"type": "string", "description": "Script name, e.g. create_aws_role.sh"},
            "role_name": {"type": "string"},
            "policy_arn": {"type": "string"},
        },
    },
    responses={201: PolicyRefreshCallbackResponseSerializer,
               400: CallbackErrorSerializer, 401: CallbackErrorSerializer},
)
@csrf_exempt
@api_view(['POST'])
@permission_classes([AllowAny])
def infrastructure_policy_refresh_callback(request: HttpRequest):
    from api.models.infrastructure import Infrastructure
    from api.models.policy_refresh_event import PolicyRefreshEvent
    from api.models.script_api_key import ScriptApiKey

    key = ScriptApiKey.authenticate(request.headers.get('X-API-Key'))
    if key is None:
        return Response({'error': 'Invalid or missing API key'},
                        status=status.HTTP_401_UNAUTHORIZED)

    account_id = request.data.get('account_id')
    if not account_id:
        return Response({'error': 'account_id is required'}, status=status.HTTP_400_BAD_REQUEST)

    # The infra link is best-effort context, not an auth gate — the API key alone
    # establishes identity. Scope the lookup to the key owner so a caller can't attribute
    # a policy-refresh record against an infrastructure they don't own. A bad/missing/
    # non-owned infra_id still records the refresh (just without the infra link).
    infra = None
    infra_id = request.data.get('infra_id')
    if infra_id:
        try:
            uuid.UUID(str(infra_id))
        except (ValueError, TypeError):
            infra_id = None
        else:
            infra = Infrastructure.objects.filter(id=infra_id, user=key.user).first()

    event = PolicyRefreshEvent.objects.create(
        user=key.user,
        infrastructure=infra,
        account_id=str(account_id)[:32],
        caller_arn=str(request.data.get('caller_arn') or ''),
        script=str(request.data.get('script') or 'create_aws_role.sh')[:64],
        role_name=str(request.data.get('role_name') or '')[:128],
        policy_arn=str(request.data.get('policy_arn') or ''),
    )
    ScriptApiKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
    logger.info(
        f"policy refresh recorded: user={key.user_id} account={account_id} "
        f"infra={infra_id} caller={event.caller_arn}"
    )
    return Response({'status': 'recorded', 'event_id': str(event.id)},
                    status=status.HTTP_201_CREATED)
