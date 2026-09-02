import logging

from api.serializers.database import DatabaseSerializer
from api.services.database_service import DatabaseService, PolicyRefreshRequiredError
from django.http import HttpRequest
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

logger = logging.getLogger(__name__)

database_service = DatabaseService()


class DatabaseResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    environment_id = serializers.UUIDField()
    name = serializers.CharField()
    engine = serializers.ChoiceField(choices=['postgres', 'mysql', 'redis', 'docdb'])
    engine_version = serializers.CharField()
    instance_class = serializers.CharField()
    allocated_storage = serializers.IntegerField(allow_null=True, help_text="GB; null for redis")
    status = serializers.CharField()
    host = serializers.CharField(allow_null=True)
    port = serializers.IntegerField(allow_null=True)
    secret_arn = serializers.CharField(allow_null=True, help_text="AWS Secrets Manager ARN — never a credential value")
    error_message = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class DatabaseCreateSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="^[a-z][a-z0-9-]{2,30}$")
    engine = serializers.ChoiceField(choices=['postgres', 'mysql', 'redis', 'docdb'])
    engine_version = serializers.CharField()
    instance_class = serializers.CharField()
    allocated_storage = serializers.IntegerField(required=False, help_text="GB; required except for redis")


class DatabaseErrorSerializer(serializers.Serializer):
    error = serializers.CharField()
    code = serializers.CharField(required=False)


def _error_response(e: Exception):
    if isinstance(e, PolicyRefreshRequiredError):
        return Response(
            {'error': str(e), 'code': 'policy_refresh_required', 'denied_actions': e.denied_actions},
            status=422,
        )
    if isinstance(e, LookupError):
        return Response({'error': str(e)}, status=status.HTTP_404_NOT_FOUND)
    if isinstance(e, PermissionError):
        return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)
    if isinstance(e, ValueError):
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    logger.error("Unhandled database view error", exc_info=e)
    return Response({'error': 'Internal error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    summary="List databases in an infrastructure",
    parameters=[OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH)],
    responses={200: DatabaseResponseSerializer(many=True)},
    methods=["GET"],
)
@extend_schema(
    summary="Create a managed database",
    description=(
        "Provisions a PostgreSQL/MySQL/Redis/DocumentDB instance inside the infrastructure's "
        "existing private subnets. Requires the environment to be ACTIVE. Runs a synchronous IAM "
        "precheck first — returns 422 with a refresh-script hint if the customer's role hasn't "
        "picked up the required permissions yet."
    ),
    parameters=[OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH)],
    request=DatabaseCreateSerializer,
    responses={202: DatabaseResponseSerializer, 400: DatabaseErrorSerializer, 422: DatabaseErrorSerializer},
    methods=["POST"],
)
@csrf_exempt
@api_view(['GET', 'POST'])
def database_list_create(request: HttpRequest, infra_id):
    if request.method == 'GET':
        try:
            dbs = database_service.list_databases(user_id=request.user.id, infra_id=infra_id)
            return Response(DatabaseSerializer.serialize_list(list(dbs)))
        except Exception as e:
            return _error_response(e)
    try:
        db = database_service.create_database(user_id=request.user.id, infra_id=infra_id, data=request.data)
        return Response(DatabaseSerializer.serialize_instance(db), status=status.HTTP_202_ACCEPTED)
    except Exception as e:
        return _error_response(e)


@extend_schema(
    summary="Get a database",
    parameters=[
        OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH),
        OpenApiParameter("database_id", OpenApiTypes.STR, OpenApiParameter.PATH),
    ],
    responses={200: DatabaseResponseSerializer, 404: DatabaseErrorSerializer},
    methods=["GET"],
)
@extend_schema(
    summary="Delete a database",
    description=(
        "Requires confirm_name to equal the database's name (typed-name confirmation). Takes a "
        "final snapshot before the underlying resource is destroyed. Works from ERROR."
    ),
    parameters=[
        OpenApiParameter("infra_id", OpenApiTypes.STR, OpenApiParameter.PATH),
        OpenApiParameter("database_id", OpenApiTypes.STR, OpenApiParameter.PATH),
    ],
    responses={202: DatabaseResponseSerializer, 400: DatabaseErrorSerializer, 404: DatabaseErrorSerializer},
    methods=["DELETE"],
)
@csrf_exempt
@api_view(['GET', 'DELETE'])
def database_detail(request: HttpRequest, infra_id, database_id):
    if request.method == 'GET':
        try:
            db = database_service.get_database(user_id=request.user.id, infra_id=infra_id, database_id=database_id)
            return Response(DatabaseSerializer.serialize_instance(db))
        except Exception as e:
            return _error_response(e)
    try:
        db = database_service.delete_database(
            user_id=request.user.id, infra_id=infra_id, database_id=database_id,
            confirm_name=request.data.get('confirm_name', ''),
        )
        return Response(DatabaseSerializer.serialize_instance(db), status=status.HTTP_202_ACCEPTED)
    except Exception as e:
        return _error_response(e)
