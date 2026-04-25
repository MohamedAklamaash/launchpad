from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from rest_framework import serializers
from api.services.application_service import ApplicationService
from api.services.application_sleep_service import ApplicationSleepService
from api.services.deployment_queue import DeploymentQueue
from api.repositories.application import ApplicationRepository
import logging

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

class AppUpdateResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField(allow_null=True)
    envs = serializers.DictField(child=serializers.CharField())
    alloted_cpu = serializers.FloatField()
    alloted_memory = serializers.FloatField()
    port = serializers.IntegerField()
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
            "status": app.status, "is_sleeping": app.is_sleeping,
            "cpu": app.alloted_cpu, "memory": app.alloted_memory, "storage": app.alloted_storage,
            "port": app.port, "url": app.project_remote_url, "branch": app.project_branch,
            "dockerfile_path": app.dockerfile_path, "build_context": app.build_context or "", "envs": app.envs,
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
        try:
            self.service.delete_application(request.user.id, pk)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except PermissionError as e:
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
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
            from api.services.infrastructure_permissions import InfrastructurePermissions
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
            from api.services.infrastructure_permissions import InfrastructurePermissions
            infra = InfrastructureRepository().get_infrastructure(app.infrastructure_id)
            if not infra or not InfrastructurePermissions.can_update_application(infra, request.user.id):
                return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)

            # Snapshot ARNs before nulling them out
            service_arn = app.service_arn
            listener_rule_arn = app.listener_rule_arn
            target_group_arn = app.target_group_arn
            task_definition_arn = app.task_definition_arn

            # Reset DB state immediately
            app.status = 'CREATED'
            app.error_message = None
            app.service_arn = None
            app.task_definition_arn = None
            app.target_group_arn = None
            app.listener_rule_arn = None
            app.save(update_fields=['status', 'error_message', 'service_arn',
                                    'task_definition_arn', 'target_group_arn', 'listener_rule_arn'])

            # Enqueue cleanup first, then deployment — worker handles sequencing
            if any([service_arn, listener_rule_arn, target_group_arn, task_definition_arn]):
                DeploymentQueue.enqueue_cleanup(
                    app_id=pk,
                    infrastructure_id=str(app.infrastructure_id),
                    service_arn=service_arn,
                    listener_rule_arn=listener_rule_arn,
                    target_group_arn=target_group_arn,
                    task_definition_arn=task_definition_arn,
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
