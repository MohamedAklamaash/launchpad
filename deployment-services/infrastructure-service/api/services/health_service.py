from django.http import JsonResponse
from django.db import connection as db_connection
from django.db.utils import OperationalError
from api.messaging.producer.producer import infra_producer
import logging

logger = logging.getLogger(__name__)

class HealthService:
    def get_health(self):
        from api.services.infrastructure import cloud_cb
        return JsonResponse({
            "status": "ok", 
            "service": "infrastructure-service",
            "circuit_breakers": {
                "cloud_provider": cloud_cb.get_state().value
            }
        }, status=200)

    def get_liveness(self):
        return JsonResponse({"status": "alive"}, status=200)

    def get_readiness(self):
        errors = {}
        # Check Database
        try:
            with db_connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
                cursor.fetchone()
        except OperationalError as e:
            logger.exception("Database readiness check failed")
            errors["database"] = str(e)

        # Check RabbitMQ Producer
        if not infra_producer.producer.is_connected():
            errors["rabbitmq_producer"] = "not connected"

        if errors:
            return JsonResponse(
                {
                    "status": "error",
                    "details": errors,
                    "service": "infrastructure-service"
                },
                status=503
            )

        return JsonResponse({
            "status": "ok",
            "message": "Application is ready to serve traffic",
            "service": "infrastructure-service"
        }, status=200)
