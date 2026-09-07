import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.models.application import Application
from api.repositories.application import ApplicationRepository
from api.services.application_service import ApplicationService
from api.services.application_sleep_service import ApplicationSleepService
from api.services.deployment_queue import DeploymentQueue

logger = logging.getLogger(__name__)


# ── Reusable inline serializers for Swagger ──────────────────────────────────

class AppListItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    cpu = serializers.FloatField()
    memory = serializers.FloatField()
    port = serializers.IntegerField()

class AppDetailSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField(allow_null=True)
    infrastructure_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=["CREATED","BUILDING","DEPLOYING","ACTIVE","SLEEPING","FAILED"])
    is_sleeping = serializers.BooleanField()
    cpu = serializers.FloatField()
    memory = serializers.FloatField()
    storage = serializers.FloatField()
    port = serializers.IntegerField()
    url = serializers.CharField(help_text="GitHub repo URL")
    branch = serializers.CharField()
    dockerfile_path = serializers.CharField()
    envs = serializers.DictField(child=serializers.CharField())
    attached_database_ids = serializers.ListField(child=serializers.UUIDField())
    deployment_url = serializers.CharField(allow_null=True)
    build_id = serializers.CharField(allow_null=True)
    error_message = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

class AppCreateSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="Application name")
    infrastructure_id = serializers.UUIDField(help_text="Target infrastructure UUID")
    project_remote_url = serializers.CharField(help_text="GitHub repo URL, e.g. https://github.com/user/repo")
    project_branch = serializers.CharField(help_text="Branch to deploy, e.g. main")
    description = serializers.CharField(required=False)
    project_commit_hash = serializers.CharField(required=False, help_text="Specific commit SHA (optional)")
    dockerfile_path = serializers.CharField(required=False, default="Dockerfile")
    port = serializers.IntegerField(required=False, default=8080)
    alloted_cpu = serializers.FloatField(required=False, default=256, help_text="CPU units: 256=0.25vCPU, 512=0.5vCPU, 1024=1vCPU")
    alloted_memory = serializers.FloatField(required=False, default=512, help_text="Memory in MB")
    envs = serializers.DictField(child=serializers.CharField(), required=False, help_text='e.g. {"NODE_ENV":"production"}')

class AppCreateResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()

class AppUpdateSerializer(serializers.Serializer):
    description = serializers.CharField(required=False)
    envs = serializers.DictField(child=serializers.CharField(), required=False, help_text='e.g. {"NODE_ENV":"production"}')
    alloted_cpu = serializers.FloatField(required=False, help_text="CPU units: 256=0.25vCPU, 512=0.5vCPU, 1024=1vCPU")
    alloted_memory = serializers.FloatField(required=False, help_text="Memory in MB")
    port = serializers.IntegerField(required=False)
    project_branch = serializers.CharField(required=False)
    dockerfile_path = serializers.CharField(required=False)
    attached_database_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False,
        help_text="Full replacement list. Does not trigger a redeploy — redeploy to apply.",
    )

class AppUpdateResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField(allow_null=True)
    envs = serializers.DictField(child=serializers.CharField())
    alloted_cpu = serializers.FloatField()
    alloted_memory = serializers.FloatField()
    port = serializers.IntegerField()
    attached_database_ids = serializers.ListField(child=serializers.UUIDField())
    updated_at = serializers.DateTimeField()

class QueuedResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    application_id = serializers.UUIDField()
    status = serializers.CharField(help_text="Always 'QUEUED'")

class SleepResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    application_id = serializers.UUIDField()
    status = serializers.CharField(help_text="Always 'SLEEPING'")

class WakeResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    application_id = serializers.UUIDField()
    status = serializers.CharField(help_text="Always 'ACTIVE'")

class ErrorSerializer(serializers.Serializer):
    error = serializers.CharField()


# ── Views ─────────────────────────────────────────────────────────────────────

class ApplicationListCreateView(APIView):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.service = ApplicationService()

    @extend_schema(
        summary="List applications for an infrastructure",
        parameters=[
            OpenApiParameter(
                name="infrastructure_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=True,
                description="UUID of the infrastructure whose applications to list",
            )
        ],
        responses={200: AppListItemSerializer(many=True), 400: ErrorSerializer},
    )
    def get(self, request):
        try:
            infra_id = request.query_params.get("infrastructure_id", "")
            if not infra_id:
                raise Exception("infrastructure_id query parameter is required")
            apps = self.service.get_user_applications(request.user.id, infra_id)
            return Response([{"id": str(a.id), "name": a.name, "cpu": a.alloted_cpu,
                               "memory": a.alloted_memory, "port": a.port} for a in apps])
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Create a new application",
        request=AppCreateSerializer,
        responses={201: AppCreateResponseSerializer, 400: ErrorSerializer, 403: ErrorSerializer, 500: ErrorSerializer},
    )
    def post(self, request):
        try:
            app = self.service.create_application(request.user, request.data)
            return Response({"id": str(app.id), "name": app.name}, status=status.HTTP_201_CREATED)
        except PermissionError as e:
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            from django.db import IntegrityError
            if isinstance(e, IntegrityError) and 'unique' in str(e).lower():
                return Response({"error": "An application with this name already exists in this infrastructure."}, status=status.HTTP_409_CONFLICT)
            logger.exception("Failed to create application")
            return Response({"error": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ApplicationDetailDeleteView(APIView):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.service = ApplicationService()

    @extend_schema(
        summary="Get application details",
        parameters=[OpenApiParameter("pk", OpenApiTypes.UUID, OpenApiParameter.PATH, description="Application UUID")],
        responses={200: AppDetailSerializer, 404: ErrorSerializer},
    )
    def get(self, request, pk=None):
        app = self.service.get_application_details(request.user.id, pk)
        if not app:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            "id": str(app.id), "name": app.name, "description": app.description,
            "infrastructure_id": str(app.infrastructure_id),
            "status": app.status, "is_sleeping": app.is_sleeping,
            "cpu": app.alloted_cpu, "memory": app.alloted_memory, "storage": app.alloted_storage,
            "port": app.port, "url": app.project_remote_url, "branch": app.project_branch,
            "dockerfile_path": app.dockerfile_path, "build_context": app.build_context or "", "envs": app.envs,
            "attached_database_ids": app.attached_database_ids or [],
            "deployment_url": app.deployment_url, "build_id": app.build_id,
            "error_message": app.error_message if app.status not in ('ACTIVE', 'SLEEPING') else None,
            "created_at": app.created_at.isoformat() if app.created_at else None,
            "updated_at": app.updated_at.isoformat() if app.updated_at else None,
        })

    @extend_schema(
        summary="Delete an application",
        parameters=[OpenApiParameter("pk", OpenApiTypes.UUID, OpenApiParameter.PATH, description="Application UUID")],
        responses={204: None, 403: ErrorSerializer, 400: ErrorSerializer},
    )
    def delete(self, request, pk=None):
        from api.services.application_service import DeploymentInProgressError
        try:
            self.service.delete_application(request.user.id, pk)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except PermissionError as e:
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except DeploymentInProgressError as e:
            return Response({"error": str(e)}, status=status.HTTP_409_CONFLICT)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class ApplicationUpdateView(APIView):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.service = ApplicationService()

    @extend_schema(
        summary="Update application configuration",
        description="Partial update — only send fields you want to change. Does not trigger redeployment.",
        parameters=[OpenApiParameter("pk", OpenApiTypes.UUID, OpenApiParameter.PATH, description="Application UUID")],
        request=AppUpdateSerializer,
        responses={200: AppUpdateResponseSerializer, 400: ErrorSerializer, 403: ErrorSerializer, 404: ErrorSerializer, 500: ErrorSerializer},
    )
    def patch(self, request, pk=None):
        try:
            updated = self.service.update_application(request.user.id, pk, request.data)
            if not updated:
                return Response({"error": "Application not found"}, status=status.HTTP_404_NOT_FOUND)
            return Response({
                "id": str(updated.id), "name": updated.name, "description": updated.description,
                "envs": updated.envs, "alloted_cpu": updated.alloted_cpu,
                "alloted_memory": updated.alloted_memory, "port": updated.port,
                "attached_database_ids": updated.attached_database_ids or [],
                "updated_at": updated.updated_at.isoformat(),
            })
        except PermissionError as e:
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception("Failed to update application")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ApplicationDeployView(APIView):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.service = ApplicationService()

    @extend_schema(
        summary="Queue application for deployment",
        description="No request body. Pushes the application onto the Redis deployment queue. The worker picks it up and runs CodeBuild + ECS deploy.",
        parameters=[OpenApiParameter("pk", OpenApiTypes.UUID, OpenApiParameter.PATH, description="Application UUID")],
        request=None,
        responses={202: QueuedResponseSerializer, 400: ErrorSerializer, 404: ErrorSerializer, 500: ErrorSerializer},
    )
    def post(self, request, pk=None):
        try:
            app = ApplicationRepository().get_by_id(pk)
            if not app:
                return Response({"error": "Application not found"}, status=status.HTTP_404_NOT_FOUND)
            from api.repositories.infrastructure import InfrastructureRepository
            from api.services.infrastructure_permissions import (
                InfrastructurePermissions,
            )
            infra = InfrastructureRepository().get_infrastructure(app.infrastructure_id)
            if not infra or not InfrastructurePermissions.can_update_application(infra, request.user.id):
                return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)
            DeploymentQueue.enqueue_deployment(pk, str(app.infrastructure_id))
            return Response({"message": "Deployment queued successfully",
                             "application_id": str(pk), "status": "QUEUED"}, status=status.HTTP_202_ACCEPTED)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception("Failed to queue deployment")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ApplicationRetryDeployView(APIView):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.service = ApplicationService()

    @extend_schema(
        summary="Retry a failed deployment",
        description="No request body. Cleans up partial AWS resources, resets status to CREATED, then re-queues for deployment.",
        parameters=[OpenApiParameter("pk", OpenApiTypes.UUID, OpenApiParameter.PATH, description="Application UUID")],
        request=None,
        responses={202: QueuedResponseSerializer, 403: ErrorSerializer, 404: ErrorSerializer, 500: ErrorSerializer},
    )
    def post(self, request, pk=None):
        try:
            app = ApplicationRepository().get_by_id(pk)
            if not app:
                return Response({"error": "Application not found"}, status=status.HTTP_404_NOT_FOUND)
            from api.repositories.infrastructure import InfrastructureRepository
            from api.services.infrastructure_permissions import (
                InfrastructurePermissions,
            )
            infra = InfrastructureRepository().get_infrastructure(app.infrastructure_id)
            if not infra or not InfrastructurePermissions.can_update_application(infra, request.user.id):
                return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)

            # Snapshot deployment handles before nulling them out
            service_arn = app.service_arn
            listener_rule_arn = app.listener_rule_arn
            target_group_arn = app.target_group_arn
            task_definition_arn = app.task_definition_arn
            runtime_refs = app.runtime_refs

            # Reset DB state immediately
            app.status = 'CREATED'
            app.error_message = None
            app.service_arn = None
            app.task_definition_arn = None
            app.target_group_arn = None
            app.listener_rule_arn = None
            app.runtime_refs = None
            app.save(update_fields=['status', 'error_message', 'service_arn',
                                    'task_definition_arn', 'target_group_arn', 'listener_rule_arn',
                                    'runtime_refs'])

            # Enqueue cleanup first, then deployment — worker handles sequencing
            if any([service_arn, listener_rule_arn, target_group_arn, task_definition_arn, runtime_refs]):
                DeploymentQueue.enqueue_cleanup(
                    app_id=pk,
                    infrastructure_id=str(app.infrastructure_id),
                    service_arn=service_arn,
                    listener_rule_arn=listener_rule_arn,
                    target_group_arn=target_group_arn,
                    task_definition_arn=task_definition_arn,
                    runtime=(runtime_refs or {}).get('runtime'),
                    refs=runtime_refs,
                )
            DeploymentQueue.enqueue_deployment(pk, str(app.infrastructure_id))
            return Response({"message": "Deployment retry queued successfully",
                             "application_id": str(pk), "status": "QUEUED"}, status=status.HTTP_202_ACCEPTED)
        except Exception as e:
            logger.exception("Failed to retry deployment")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ApplicationSleepView(APIView):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sleep_service = ApplicationSleepService()
        self.app_repo = ApplicationRepository()

    @extend_schema(
        summary="Put application to sleep",
        description="No request body. Scales ECS desired task count to 0. URL stays registered but stops serving traffic.",
        parameters=[OpenApiParameter("pk", OpenApiTypes.UUID, OpenApiParameter.PATH, description="Application UUID")],
        request=None,
        responses={200: SleepResponseSerializer, 400: ErrorSerializer, 403: ErrorSerializer, 404: ErrorSerializer, 500: ErrorSerializer},
    )
    def post(self, request, pk=None):
        try:
            app = self.app_repo.get_by_id(pk)
            if not app:
                return Response({"error": "Application not found"}, status=status.HTTP_404_NOT_FOUND)
            if str(app.user_id) != str(request.user.id):
                return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)
            self.sleep_service.sleep_application(app)
            return Response({"message": "Application put to sleep successfully",
                             "application_id": str(pk), "status": "SLEEPING"})
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception("Failed to sleep application")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ApplicationWakeView(APIView):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sleep_service = ApplicationSleepService()
        self.app_repo = ApplicationRepository()

    @extend_schema(
        summary="Wake application from sleep",
        description="No request body. Restores ECS desired task count to the previously configured value.",
        parameters=[OpenApiParameter("pk", OpenApiTypes.UUID, OpenApiParameter.PATH, description="Application UUID")],
        request=None,
        responses={200: WakeResponseSerializer, 400: ErrorSerializer, 403: ErrorSerializer, 404: ErrorSerializer, 500: ErrorSerializer},
    )
    def post(self, request, pk=None):
        try:
            app = self.app_repo.get_by_id(pk)
            if not app:
                return Response({"error": "Application not found"}, status=status.HTTP_404_NOT_FOUND)
            if str(app.user_id) != str(request.user.id):
                return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)
            self.sleep_service.wake_application(app)
            return Response({"message": "Application woken up successfully",
                             "application_id": str(pk), "status": "ACTIVE"})
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception("Failed to wake application")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ── GitHub webhook (unauthenticated; HMAC-verified) ──────────────────────────

class WebhookSecretResponseSerializer(serializers.Serializer):
    webhook_url = serializers.CharField()
    secret = serializers.CharField(help_text="Shown only at issue/rotate time; not retrievable later")
    instructions = serializers.CharField()


@csrf_exempt
@api_view(['POST'])
@permission_classes([AllowAny])
def application_github_webhook(request, app_id: str):
    # GitHub sends X-GitHub-Event and X-Hub-Signature-256: sha256=<hexdigest>.
    # We verify the per-app HMAC before doing anything else — never trust the payload first.
    event_type = request.headers.get('X-GitHub-Event', '')
    signature_header = request.headers.get('X-Hub-Signature-256', '')

    if not signature_header.startswith('sha256='):
        return Response({"error": "Missing or malformed X-Hub-Signature-256"},
                        status=status.HTTP_401_UNAUTHORIZED)

    try:
        app = Application.objects.get(id=app_id)
    except Application.DoesNotExist:
        return Response({"error": "Application not found"}, status=status.HTTP_404_NOT_FOUND)

    if not app.github_webhook_secret:
        return Response({"error": "Webhook not configured for this application"},
                        status=status.HTTP_403_FORBIDDEN)

    expected_signature = 'sha256=' + hmac.new(
        app.github_webhook_secret.encode('utf-8'),
        request.body,
        hashlib.sha256,
    ).hexdigest()

    # Constant-time compare avoids leaking the secret via timing side channels.
    if not hmac.compare_digest(signature_header, expected_signature):
        logger.warning(f"GitHub webhook signature mismatch for app {app_id}")
        return Response({"error": "Invalid signature"}, status=status.HTTP_401_UNAUTHORIZED)

    # GitHub sends a "ping" when the webhook is first configured; ack it without enqueuing.
    if event_type == 'ping':
        return Response({"status": "pong"}, status=status.HTTP_200_OK)
    if event_type != 'push':
        return Response({"status": "ignored", "event": event_type}, status=status.HTTP_200_OK)

    # GitHub's default content type (application/x-www-form-urlencoded) wraps the JSON body in a
    # `payload` form field; application/json sends it directly. Unwrap the form case so the ref
    # check sees the real event instead of silently treating every push as branch-less.
    payload = request.data if isinstance(request.data, dict) else {}
    if 'ref' not in payload and 'payload' in payload:
        try:
            payload = json.loads(payload['payload'])
        except (TypeError, ValueError):
            payload = None
        if not isinstance(payload, dict):
            logger.warning(f"GitHub webhook for app {app_id}: unparseable form payload")
            return Response({"error": "Invalid payload"}, status=status.HTTP_400_BAD_REQUEST)

    # An app with no tracked branch has no deploy target — ignore rather than deploy every branch.
    if not app.project_branch:
        logger.info(f"GitHub webhook for app {app_id}: no tracked branch configured; ignoring push")
        return Response({"status": "ignored", "reason": "no tracked branch configured"},
                        status=status.HTTP_200_OK)

    # Only redeploy when the push targets the branch this app actually tracks.
    pushed_ref = payload.get('ref', '')
    expected_ref = f"refs/heads/{app.project_branch}"
    if pushed_ref != expected_ref:
        logger.info(f"GitHub webhook for app {app_id}: ignoring push to {pushed_ref} (tracks {expected_ref})")
        return Response(
            {"status": "ignored", "reason": f"pushed ref {pushed_ref} != tracked {expected_ref}"},
            status=status.HTTP_200_OK,
        )

    # GitHub redelivers the same push (manual replay, or its own retry on a slow
    # response) with a stable X-GitHub-Delivery GUID. Without dedup each redelivery
    # triggers another full deployment. SETNX keeps the first delivery and drops
    # repeats within the TTL window; a Redis hiccup falls through to enqueue (we'd
    # rather double-deploy than silently drop a real push).
    delivery_id = request.headers.get('X-GitHub-Delivery', '')
    if delivery_id:
        try:
            first_seen = DeploymentQueue.get_redis().set(
                f"gh:webhook:delivery:{app_id}:{delivery_id}", "1",
                nx=True, ex=3600,
            )
            if not first_seen:
                return Response(
                    {"status": "duplicate", "application_id": str(app_id)},
                    status=status.HTTP_200_OK,
                )
        except Exception:
            logger.warning(f"GitHub webhook dedup check failed for app {app_id}; proceeding")

    try:
        DeploymentQueue.enqueue_deployment(str(app_id), str(app.infrastructure_id))
    except Exception:
        logger.exception(f"GitHub webhook failed to enqueue deployment for app {app_id}")
        return Response({"error": "Failed to enqueue deployment"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response({"status": "accepted", "application_id": str(app_id)},
                    status=status.HTTP_202_ACCEPTED)


@extend_schema(
    summary="Issue or rotate GitHub webhook secret",
    description=(
        "Generates a fresh per-app HMAC secret for verifying GitHub push webhooks. "
        "The secret is returned ONCE and is not retrievable afterwards — paste it into "
        "GitHub's webhook UI immediately. Calling this again rotates the secret."
    ),
    parameters=[OpenApiParameter("app_id", OpenApiTypes.UUID, OpenApiParameter.PATH, description="Application UUID")],
    request=None,
    responses={200: WebhookSecretResponseSerializer, 403: ErrorSerializer, 404: ErrorSerializer},
)
@api_view(['POST'])
def application_rotate_webhook_secret(request, app_id: str):
    app = ApplicationRepository().get_by_id(app_id)
    if not app:
        return Response({"error": "Application not found"}, status=status.HTTP_404_NOT_FOUND)
    if str(app.user_id) != str(request.user.id):
        return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)

    # Gateway is mounted at /api (see gateway-service/main.py), so the public URL we hand
    # to GitHub uses /api/webhooks/... — NOT /api/v1/, which is the internal upstream path.
    public_base = getattr(settings, 'PUBLIC_GATEWAY_URL', '').rstrip('/')
    if not public_base:
        # Without a public base we'd hand GitHub a relative /api/webhooks/... URL that
        # silently never delivers. Fail loudly instead of issuing a dead webhook.
        logger.error("PUBLIC_GATEWAY_URL is not configured; cannot build a webhook URL")
        return Response({"error": "Webhook URL is not configured on the server"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    secret = app.issue_webhook_secret()
    webhook_url = f"{public_base}/api/webhooks/github/{app_id}"
    return Response({
        "webhook_url": webhook_url,
        "secret": secret,
        "instructions": (
            "In GitHub: Settings > Webhooks > Add webhook. Paste the URL and secret, "
            "set Content type to application/json, and select the 'Just the push event' trigger."
        ),
    }, status=status.HTTP_200_OK)
