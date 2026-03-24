from django.http import JsonResponse
from django.db import connection as db_connection
from django.db.utils import OperationalError
from api.common.envs.application import app_config
import pika
import logging

logger = logging.getLogger(__name__)

class HealthService:
    def get_health(self):
        from api.services.application_service import ApplicationService
        service = ApplicationService()
        return JsonResponse({
            "status": "ok", 
            "service": "application-service",
            "circuit_breakers": {
                "user_service": service.user_client.breaker.get_state().value,
                "infra_service": service.infra_client.breaker.get_state().value
            }
        }, status=200)

    def get_liveness(self):
        return JsonResponse({"status": "alive", "service": "application-service"}, status=200)

    def get_readiness(self):
        errors = {}
        try:
            with db_connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
                cursor.fetchone()
        except OperationalError as e:
            logger.exception("Database readiness check failed")
            errors["database"] = str(e)

        try:
            parameters = pika.URLParameters(app_config.rabbitmq_url)
            rabbit_connection = pika.BlockingConnection(parameters)
            rabbit_connection.close()
        except Exception as e:
            logger.exception("RabbitMQ readiness check failed")
            errors["rabbitmq"] = str(e)

        if errors:
            return JsonResponse(
                {
                    "status": "error",
                    "details": errors,
                    "service": "application-service"
                },
                status=503
            )

        return JsonResponse({
            "status": "ok",
            "message": "Application is ready to serve traffic",
            "service": "application-service"
        }, status=200)
